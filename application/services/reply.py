"""Replies in either direction through DeliveryPipeline and multi-ID mappings."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from application.services.delivery import DeliveryPart, DeliveryPipeline, DeliveryPlan
from domain.models import Attachment, AttachmentKind, ChannelRef, MessageRef
from ports.channel import ChannelPort
from ports.repositories import RepositoryBundle
from presentation.formatters import format_reply_relay_text

logger = logging.getLogger(__name__)
CAPTIONLESS = frozenset((AttachmentKind.STICKER, AttachmentKind.VIDEO_NOTE))


@dataclass(frozen=True, slots=True)
class ReplyRelayRequest:
    current_chat_id: int
    current_message_id: int
    replied_message_id: int
    sender_label: str
    text: str | None = None
    attachments: Iterable[Attachment] = field(default_factory=tuple)
    captionless_media: bool = False
    channel: str = "telegram"
    current_topic_id: int | None = None
    current_message_ids: tuple[int, ...] = ()
    # Видимое имя автора. Используется вместо sender_label (который может быть
    # анонимным), если диалог начат заявкой «В гости»/«Вписаться».
    open_label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", tuple(self.attachments))


@dataclass(frozen=True, slots=True)
class ReplyRelayResult:
    delivered: bool
    skipped: bool = False
    user_error: str | None = None
    target_chat_id: int | None = None
    target_message_id: int | None = None
    delivered_message_id: int | None = None
    delivered_message_ids: tuple[int, ...] = ()
    partial: bool = False


@dataclass(frozen=True, slots=True)
class _ReplyTarget:
    chat_id: int
    message_id: int
    topic_id: int | None
    open_identity: bool = False


class ReplyService:
    def __init__(self, repositories: RepositoryBundle, channel: ChannelPort) -> None:
        self._repositories = repositories
        self._delivery = DeliveryPipeline(repositories, channel)

    async def relay_reply(self, request: ReplyRelayRequest) -> ReplyRelayResult:
        """Mongo delivery claims serialize duplicate sends across workers/processes.

        A per-message in-memory lock used to grow without bound here; it is
        unnecessary because durable claims are acquired before Telegram sends.
        """
        try:
            target = await self._resolve_target(request)
        except Exception:
            logger.exception("Reply mapping lookup failed")
            return ReplyRelayResult(False, user_error="Не доставлено 💔 Ошибка поиска диалога.")
        if target is None:
            return ReplyRelayResult(False, skipped=True)

        target_ref = ChannelRef(request.channel, target.chat_id, topic_id=target.topic_id)
        origin = MessageRef(
            ChannelRef(request.channel, request.current_chat_id, topic_id=request.current_topic_id),
            request.current_message_id,
        )
        reply_to = MessageRef(target_ref, target.message_id)
        label = request.open_label if (target.open_identity and request.open_label) else request.sender_label
        caption = format_reply_relay_text(label, request.text)
        attachments = tuple(request.attachments)
        if len(attachments) > 1:
            parts = ((DeliveryPart("text", text=caption),
                      DeliveryPart("album", attachments=attachments)) if len(caption) > 1000
                     else (DeliveryPart("album", text=caption, attachments=attachments),))
        elif attachments and (request.captionless_media or attachments[0].kind in CAPTIONLESS):
            parts = (DeliveryPart("text", text=caption),
                     DeliveryPart("media", attachments=attachments))
        elif attachments:
            parts = ((DeliveryPart("text", text=caption),
                      DeliveryPart("media", attachments=attachments)) if len(caption) > 1000
                     else (DeliveryPart("media", text=caption, attachments=attachments),))
        else:
            parts = (DeliveryPart("text", text=caption),)
        outcome = await self._delivery.deliver(DeliveryPlan(
            origin=origin, target=target_ref, parts=parts, reply_to=reply_to,
            origin_message_ids=request.current_message_ids,
            count_stat=False,
            open_identity=target.open_identity,
        ))
        ids = tuple(int(ref.message_id) for ref in outcome.sent)
        return ReplyRelayResult(
            delivered=outcome.delivered,
            user_error=outcome.user_error,
            target_chat_id=target.chat_id,
            target_message_id=target.message_id,
            delivered_message_id=ids[0] if ids else None,
            delivered_message_ids=ids,
            partial=outcome.partial,
        )

    async def _resolve_target(self, request: ReplyRelayRequest) -> _ReplyTarget | None:
        kwargs = {"channel": request.channel} if request.channel != "telegram" else {}
        thread = await self._repositories.threads.find_thread_by_relay(
            request.current_chat_id, request.replied_message_id, **kwargs
        )
        if not thread:
            thread = await self._repositories.threads.find_thread_by_origin(
                request.current_chat_id, request.replied_message_id, **kwargs
            )
        if not thread:
            return None

        relay_ids = thread.get("relay_message_ids") or [thread["relay_message_id"]]
        origin_ids = thread.get("origin_message_ids") or [thread["origin_message_id"]]
        if request.current_chat_id == thread["relay_chat_id"] and request.replied_message_id in relay_ids:
            chat_id = int(thread["origin_chat_id"])
            msg_id = int(thread.get("origin_anchor_message_id") or thread["origin_message_id"])
            topic_id = thread.get("origin_topic_id")
        elif request.current_chat_id == thread["origin_chat_id"] and request.replied_message_id in origin_ids:
            chat_id = int(thread["relay_chat_id"])
            msg_id = int(thread["relay_message_id"])
            topic_id = thread.get("relay_topic_id")
        else:
            return None

        # Backward compatibility with existing threads written before Stage 2.
        if topic_id is None and "origin_topic_id" not in thread and "relay_topic_id" not in thread:
            chat = await self._repositories.chats.get_chat_by_id(chat_id)
            topic_id = chat.get("topic_id") if chat else None
        return _ReplyTarget(
            chat_id, msg_id, int(topic_id) if topic_id else None,
            bool(thread.get("open_identity")),
        )
