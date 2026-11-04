"""Unified relay service: group/DM text, media, album and forward."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from application.services.delivery import DeliveryPart, DeliveryPipeline, DeliveryPlan
from application.services.identity import sender_label_for_chat
from application.services.routing import resolve_destination, group_destination_allowed, dm_destination_allowed
from domain.models import Attachment, AttachmentKind, ChannelRef, MessageRef
from ports.channel import ChannelPort
from ports.repositories import RepositoryBundle
from presentation.formatters import (
    chat_relay_header, escape_html, format_chat_relay_text,
    format_dm_media_header, format_dm_relay_text,
)

logger = logging.getLogger(__name__)
CAPTIONLESS = frozenset((AttachmentKind.STICKER, AttachmentKind.VIDEO_NOTE))


@dataclass(frozen=True, slots=True)
class RelayTextRequest:
    origin_chat_id: int
    origin_message_id: int
    target_chat: dict[str, Any]
    text: str
    sender_label: str
    sender_user_id: int | None = None
    origin_topic_id: int | None = None
    attachments: tuple[Attachment, ...] = ()
    origin_message_ids: tuple[int, ...] = ()
    forward_source: MessageRef | None = None
    forward_as_copy: bool = False


@dataclass(frozen=True, slots=True)
class RelayResult:
    delivered: bool
    skipped: bool = False
    user_error: str | None = None
    relay_chat_id: int | None = None
    relay_message_id: int | None = None
    migrated_to_chat_id: int | None = None
    partial: bool = False
    relay_message_ids: tuple[int, ...] = ()


class RelayService:
    """Policy and formatting over the common DeliveryPipeline."""

    def __init__(self, repositories: RepositoryBundle, channel: ChannelPort) -> None:
        self._repositories = repositories
        self._pipeline = DeliveryPipeline(repositories, channel)

    async def relay_group_text(self, request: RelayTextRequest) -> RelayResult:
        return await self.relay_group(request)

    async def relay_group(self, request: RelayTextRequest) -> RelayResult:
        source = await self._repositories.chats.get_chat_by_id(request.origin_chat_id)
        if not source:
            logger.warning("Unregistered origin chat_id=%s", request.origin_chat_id)
            return RelayResult(False, user_error="Чат не зарегистрирован в боте 💔")
        if source.get("group") == "special":
            return RelayResult(False, user_error="Не доставлено 💔\nОшибка: специальные беседы не могут отправлять сообщения")
        if source.get("topic_id") and source["topic_id"] != request.origin_topic_id:
            return RelayResult(False, skipped=True)
        actual = await resolve_destination(self._repositories.chats, request.target_chat)
        if not group_destination_allowed(source, request.target_chat, actual):
            return RelayResult(False, skipped=True)
        if actual["chat_id"] == source["chat_id"]:
            return RelayResult(False, skipped=True)

        if request.attachments or request.forward_source:
            header = chat_relay_header(source["name"], request.sender_label)
            if request.text:
                header += "\n" + escape_html(request.text)
        else:
            header = format_chat_relay_text(source["name"], request.sender_label, request.text)
        return await self._send(request, actual, header)

    async def relay_dm_text(self, request: RelayTextRequest) -> RelayResult:
        return await self.relay_dm(request)

    async def relay_dm(self, request: RelayTextRequest) -> RelayResult:
        # fail-closed, for text AND media and all DM routes
        label = await sender_label_for_chat(
            self._repositories.dm, "private", request.sender_user_id, request.sender_label
        )
        actual = await resolve_destination(self._repositories.chats, request.target_chat)
        if not dm_destination_allowed(request.target_chat, actual):
            return RelayResult(False, skipped=True)
        header = (
            format_dm_media_header(label, request.text)
            if request.attachments or request.forward_source else format_dm_relay_text(label, request.text)
        )
        # Telegram's regular forward exposes original attribution. Anonymous
        # messages use copyMessage, which strips the forwarded-from attribution.
        if request.forward_source and label == "анонима":
            request = replace(request, forward_as_copy=True)
        return await self._send(request, actual, header)

    async def _send(self, request: RelayTextRequest, actual: dict, header: str) -> RelayResult:
        plan = self._plan(request, actual, header)
        outcome = await self._pipeline.deliver(plan)
        migrated = None
        if not outcome.delivered and self._is_migration_error(outcome.send_exception):
            new_id = getattr(outcome.send_exception, "migrate_to_chat_id", None)
            if new_id:
                old_id = actual["chat_id"]
                try:
                    await self._repositories.chats.update_chat(old_id, {"chat_id": new_id})
                    actual = {**actual, "chat_id": int(new_id)}
                    migrated = int(new_id)
                    outcome = await self._pipeline.deliver(self._plan(request, actual, header))
                except Exception:
                    logger.exception("Cannot persist chat migration %s -> %s", old_id, new_id)
                    return RelayResult(False, user_error="Не доставлено 💔 Ошибка обновления чата.")
        ids = tuple(int(ref.message_id) for ref in outcome.sent)
        return RelayResult(
            delivered=outcome.delivered,
            user_error=outcome.user_error,
            relay_chat_id=int(actual["chat_id"]) if ids else None,
            relay_message_id=ids[0] if ids else None,
            migrated_to_chat_id=migrated,
            partial=outcome.partial,
            relay_message_ids=ids,
        )

    def _plan(self, request: RelayTextRequest, actual: dict, header: str) -> DeliveryPlan:
        target = ChannelRef("telegram", actual["chat_id"], topic_id=actual.get("topic_id"))
        origin = MessageRef(
            ChannelRef("telegram", request.origin_chat_id, topic_id=request.origin_topic_id),
            request.origin_message_id,
        )
        if request.forward_source:
            parts = (
                DeliveryPart("text", text=header),
                DeliveryPart("copy" if request.forward_as_copy else "forward",
                             forward_source=request.forward_source),
            )
        elif len(request.attachments) > 1:
            if len(header) > 1000:
                parts = (DeliveryPart("text", text=header),
                         DeliveryPart("album", attachments=request.attachments))
            else:
                parts = (DeliveryPart("album", text=header, attachments=request.attachments),)
        elif request.attachments and request.attachments[0].kind in CAPTIONLESS:
            parts = (
                DeliveryPart("text", text=header),
                DeliveryPart("media", attachments=request.attachments),
            )
        elif request.attachments:
            if len(header) > 1000:
                parts = (DeliveryPart("text", text=header),
                         DeliveryPart("media", attachments=request.attachments))
            else:
                parts = (DeliveryPart("media", text=header, attachments=request.attachments),)
        else:
            parts = (DeliveryPart("text", text=header),)
        return DeliveryPlan(
            origin=origin,
            target=target,
            claim_target=ChannelRef("telegram", request.target_chat["chat_id"]),
            parts=parts,
            origin_message_ids=request.origin_message_ids,
            origin_anchor_message_id=(int(request.forward_source.message_id)
                                      if request.forward_source and request.forward_source.chat_id == origin.chat_id
                                      else request.origin_message_id),
        )

    @staticmethod
    def _is_migration_error(exc: Exception | None) -> bool:
        return bool(exc and ("migrate" in str(exc).lower() or
                             "TelegramMigrateToChat" in type(exc).__name__))
