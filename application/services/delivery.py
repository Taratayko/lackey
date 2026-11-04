"""Single outbound path for text, media, albums, forwards and threaded replies.

A delivered Telegram message is NEVER retried due to mapping/statistics failure.
All visible message IDs (including captions/headers) belong to one thread record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from application.services.delivery_receipt import MAPPING_WARNING, finalize_delivery
from application.services.delivery_identity import delivery_id
from domain.models import Attachment, ChannelRef, MessageRef, OutgoingMessage
from ports.channel import ChannelPort
from ports.repositories import RepositoryBundle

logger = logging.getLogger(__name__)

class PartialDispatchError(Exception):
    """An adapter already sent some messages before a later sub-send failed."""

    def __init__(self, sent: tuple[MessageRef, ...], original: Exception) -> None:
        super().__init__(str(original))
        self.sent = sent
        self.original = original


PARTIAL_WARNING = "Доставлено частично. Проверьте целевую беседу; не отправляйте повторно автоматически."
SEND_ERROR = "Не доставлено 💔 Проверьте доступ бота к чату."


@dataclass(frozen=True, slots=True)
class DeliveryPart:
    kind: Literal["text", "media", "album", "forward", "copy"]
    text: str | None = None   # HTML-escaped by the presentation layer
    attachments: tuple[Attachment, ...] = ()
    forward_source: MessageRef | None = None


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    origin: MessageRef
    target: ChannelRef
    parts: tuple[DeliveryPart, ...]
    origin_message_ids: tuple[int, ...] = ()
    claim_target: ChannelRef | None = None  # stable requested route before redirect/migration
    origin_anchor_message_id: int | None = None
    reply_to: MessageRef | None = None
    count_stat: bool = True
    mark_reaction: bool = True


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    delivered: bool
    partial: bool = False
    user_error: str | None = None
    sent: tuple[MessageRef, ...] = ()
    send_exception: Exception | None = field(default=None, repr=False)


class DeliveryPipeline:
    """Shared dispatch + durable mapping + nonfatal post-send bookkeeping."""

    def __init__(self, repositories: RepositoryBundle, channel: ChannelPort) -> None:
        self._repositories = repositories
        self._channel = channel

    async def deliver(self, plan: DeliveryPlan) -> DeliveryOutcome:
        claims = self._repositories.delivery_claims
        key = delivery_id(plan.origin, plan.claim_target or plan.target, plan.origin_message_ids)
        if claims is not None:
            try:
                acquired = await claims.claim(key, plan.origin, plan.claim_target or plan.target)
            except Exception:
                logger.exception("Cannot persist delivery claim; fail closed before Telegram send")
                return DeliveryOutcome(False, user_error="Не доставлено: MongoDB недоступна. Отправка не начиналась.")
            if not acquired:
                # Telegram may redeliver an update. Never repeat the side effect.
                logger.info("Duplicate delivery suppressed: %s", key)
                return DeliveryOutcome(False)
        sent: list[MessageRef] = []
        send_failure = None
        for part in plan.parts:
            try:
                sent.extend(await self._send_part(plan, part))
            except Exception as exc:
                if isinstance(exc, PartialDispatchError):
                    sent.extend(exc.sent)
                logger.exception("Outbound %s failed after %s sent messages", part.kind, len(sent))
                send_failure = exc
                break

        if not sent:
            if claims is not None:
                try:
                    if (send_failure is not None
                            and getattr(send_failure, "migrate_to_chat_id", None)):
                        # Official Telegram migration rejection proves no delivery.
                        await claims.release_for_migration(key)
                    else:
                        await claims.complete(key, delivered=False, partial=False, relay_ids=())
                except Exception:
                    logger.exception("Cannot finalize failed delivery claim %s", key)
            return DeliveryOutcome(False, user_error=SEND_ERROR, send_exception=send_failure)

        def native_id(value: str, channel: str):
            return int(value) if channel == "telegram" else str(value)

        origin_ids = tuple(dict.fromkeys((native_id(plan.origin.message_id, plan.origin.channel),
                                         *(native_id(str(mid), plan.origin.channel)
                                           for mid in plan.origin_message_ids))))
        relay_ids = tuple(dict.fromkeys(native_id(ref.message_id, ref.channel) for ref in sent))
        relay_topic = next((int(r.topic_id) for r in sent if r.topic_id), None)
        if relay_topic is None and plan.target.topic_id:
            relay_topic = int(plan.target.topic_id)

        async def save_mapping() -> None:
            await self._repositories.threads.save_thread(
                origin_chat_id=native_id(plan.origin.chat_id, plan.origin.channel),
                origin_message_id=native_id(plan.origin.message_id, plan.origin.channel),
                relay_chat_id=native_id(plan.target.chat_id, plan.target.channel.value),
                relay_message_id=relay_ids[0],
                origin_message_ids=list(origin_ids),
                relay_message_ids=list(relay_ids),
                origin_anchor_message_id=(plan.origin_anchor_message_id or native_id(plan.origin.message_id, plan.origin.channel)),
                origin_topic_id=int(plan.origin.topic_id) if plan.origin.topic_id else None,
                relay_topic_id=relay_topic,
                origin_channel=plan.origin.channel,
                relay_channel=plan.target.channel.value,
            )

        async def add_stat() -> None:
            await self._repositories.stats.increment_stat(
                native_id(plan.origin.chat_id, plan.origin.channel),
                native_id(plan.target.chat_id, plan.target.channel.value),
            )

        async def reaction() -> None:
            await self._channel.set_reaction(plan.origin, "✍")

        mapping_saved = await finalize_delivery(
            save_mapping=save_mapping,
            increment_statistics=add_stat if plan.count_stat else None,
            mark_reaction=reaction if plan.mark_reaction and send_failure is None else None,
        )
        if claims is not None:
            try:
                await claims.complete(
                    key, delivered=True, partial=send_failure is not None,
                    relay_ids=tuple(str(mid) for mid in relay_ids),
                )
            except Exception:
                logger.exception("Message delivered, but claim finalization failed: %s", key)
        if send_failure is not None:
            return DeliveryOutcome(True, True, PARTIAL_WARNING if mapping_saved else
                                   f"{PARTIAL_WARNING} {MAPPING_WARNING}", tuple(sent), send_failure)
        return DeliveryOutcome(True, user_error=None if mapping_saved else MAPPING_WARNING, sent=tuple(sent))

    async def _send_part(self, plan: DeliveryPlan, part: DeliveryPart) -> tuple[MessageRef, ...]:
        if part.kind == "text":
            return (await self._channel.send_text(plan.target, part.text or "", reply_to=plan.reply_to),)
        if part.kind == "media":
            if len(part.attachments) != 1:
                raise ValueError("Single media part requires one attachment")
            msg = OutgoingMessage(
                ref=MessageRef(plan.target, plan.origin.message_id),
                text=part.text,
                attachments=part.attachments,
                reply_to=plan.reply_to,
            )
            return (await self._channel.send_message(msg),)
        if part.kind == "album":
            return tuple(await self._channel.send_album(
                target=plan.target,
                attachments=part.attachments,
                caption=part.text,
                reply_to=plan.reply_to,
            ))
        if part.kind == "forward" and part.forward_source is not None:
            return (await self._channel.forward_message(plan.target, part.forward_source),)
        if part.kind == "copy" and part.forward_source is not None:
            return (await self._channel.copy_message(plan.target, part.forward_source),)
        raise ValueError(f"Invalid delivery part: {part.kind}")
