"""Web/Site payload ↔ domain mapper.

Маппер не знает про HTTP-framework и Mongo. Его задача — привести JSON от
будущего сайта к универсальным доменным моделям communication core.
"""
from __future__ import annotations

from typing import Any

from domain.models import (
    Attachment,
    ChannelKind,
    ChannelRef,
    IncomingMessage,
    MessageAuthor,
    MessageRef,
    OutgoingMessage,
)

from .schemas import WebAttachmentPayload, WebIncomingMessagePayload


def web_attachment_to_domain(payload: WebAttachmentPayload) -> Attachment:
    """Преобразовать web attachment DTO в доменное Attachment."""
    return Attachment(
        kind=payload.kind,
        external_file_id=payload.file_id,
        url=payload.url,
        filename=payload.filename,
        mime_type=payload.mime_type,
        size=payload.size,
        meta={"web": payload.meta},
    )


def web_payload_to_domain(payload: WebIncomingMessagePayload) -> IncomingMessage:
    """Преобразовать сообщение сайта в IncomingMessage."""
    channel_ref = ChannelRef(
        channel=ChannelKind.WEB,
        external_chat_id=payload.community_id,
        topic_id=payload.topic_id,
        title=payload.meta.get("community_title"),
    )
    reply_to = None
    if payload.reply_to_message_id is not None:
        reply_to = MessageRef(channel_ref=channel_ref, external_message_id=payload.reply_to_message_id)

    return IncomingMessage(
        ref=MessageRef(channel_ref=channel_ref, external_message_id=payload.message_id),
        author=MessageAuthor(
            external_user_id=payload.author.user_id,
            username=payload.author.username,
            display_name=payload.author.display_name,
            meta={
                "web_session_id": payload.author.session_id,
                "web_author_meta": payload.author.meta,
            },
        ),
        text=payload.text,
        attachments=tuple(web_attachment_to_domain(item) for item in payload.attachments),
        reply_to=reply_to,
        raw=payload.raw,
        meta={"web": payload.meta},
    )


def web_dict_to_domain(data: dict[str, Any]) -> IncomingMessage:
    """Быстрый helper для REST-controller слоя."""
    return web_payload_to_domain(WebIncomingMessagePayload.from_dict(data))


def domain_message_to_web_dict(message: IncomingMessage | OutgoingMessage) -> dict[str, Any]:
    """Преобразовать доменное сообщение в JSON-friendly dict для сайта."""
    author = None
    if message.author is not None:
        author = {
            "user_id": message.author.external_user_id,
            "username": message.author.username,
            "display_name": message.author.display_name,
            "kind": message.author.kind.value,
            "meta": message.author.meta,
        }

    return {
        "channel": message.ref.channel_ref.channel.value,
        "community_id": message.ref.channel_ref.external_chat_id,
        "topic_id": message.ref.channel_ref.topic_id,
        "message_id": message.ref.external_message_id,
        "reply_to_message_id": message.reply_to.external_message_id if message.reply_to else None,
        "author": author,
        "text": message.text or "",
        "attachments": [
            {
                "kind": attachment.kind.value,
                "file_id": attachment.external_file_id,
                "url": attachment.url,
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "size": attachment.size,
                "meta": attachment.meta,
            }
            for attachment in message.attachments
        ],
        "direction": message.direction.value,
        "meta": message.meta,
    }
