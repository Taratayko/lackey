"""VK event → domain message mapper.

Здесь нет сетевых вызовов и нет зависимости от Telegram. Маппер принимает
сырой update из VK Bots Long Poll/Callback API и возвращает доменную модель,
которую позже можно будет отдать в application services.
"""
from __future__ import annotations

from typing import Any

from domain.models import (
    Attachment,
    AttachmentKind,
    ChannelKind,
    ChannelRef,
    IncomingMessage,
    MessageAuthor,
    MessageRef,
)


def _vk_attachment_external_id(raw: dict[str, Any]) -> str | None:
    """Собрать стабильный attachment-id VK вида type_owner_id_id_access_key."""
    kind = raw.get("type")
    if not kind:
        return None
    payload = raw.get(kind) or {}
    owner_id = payload.get("owner_id")
    item_id = payload.get("id")
    if owner_id is None or item_id is None:
        return None
    access_key = payload.get("access_key")
    base = f"{kind}{owner_id}_{item_id}"
    return f"{base}_{access_key}" if access_key else base


def _attachment_kind(vk_type: str | None) -> AttachmentKind:
    match vk_type:
        case "photo":
            return AttachmentKind.PHOTO
        case "video":
            return AttachmentKind.VIDEO
        case "doc":
            return AttachmentKind.DOCUMENT
        case "audio":
            return AttachmentKind.AUDIO
        case "audio_message":
            return AttachmentKind.VOICE
        case "sticker":
            return AttachmentKind.STICKER
        case _:
            return AttachmentKind.UNKNOWN


def vk_attachments_to_domain(raw_attachments: list[dict[str, Any]] | None) -> tuple[Attachment, ...]:
    """Преобразовать VK attachments в доменные Attachment."""
    result: list[Attachment] = []
    for raw in raw_attachments or []:
        vk_type = raw.get("type")
        payload = raw.get(vk_type) or {}
        result.append(
            Attachment(
                kind=_attachment_kind(vk_type),
                external_file_id=_vk_attachment_external_id(raw),
                filename=payload.get("title") or payload.get("name"),
                url=payload.get("url"),
                meta={"vk_type": vk_type, "raw": raw},
            )
        )
    return tuple(result)


def vk_message_update_to_domain(update: dict[str, Any]) -> IncomingMessage | None:
    """Сконвертировать VK update `message_new` в IncomingMessage.

    Возвращает None для update других типов. Полный routing/relay будет
    подключаться отдельным патчем, когда VK runtime начнёт реально запускаться.
    """
    if update.get("type") != "message_new":
        return None

    obj = update.get("object") or {}
    message = obj.get("message") or obj
    peer_id = message.get("peer_id")
    message_id = message.get("id") or message.get("conversation_message_id")
    if peer_id is None or message_id is None:
        return None

    channel_ref = ChannelRef(
        channel=ChannelKind.VK,
        external_chat_id=peer_id,
        title=str(peer_id),
    )
    reply_to = None
    reply_message = message.get("reply_message")
    if isinstance(reply_message, dict):
        reply_id = reply_message.get("id") or reply_message.get("conversation_message_id")
        if reply_id is not None:
            reply_to = MessageRef(channel_ref=channel_ref, external_message_id=reply_id)

    return IncomingMessage(
        ref=MessageRef(channel_ref=channel_ref, external_message_id=message_id),
        author=MessageAuthor(
            external_user_id=message.get("from_id"),
            meta={"vk_peer_id": peer_id},
        ),
        text=message.get("text") or "",
        attachments=vk_attachments_to_domain(message.get("attachments")),
        reply_to=reply_to,
        raw=update,
        meta={
            "vk_peer_id": peer_id,
            "vk_conversation_message_id": message.get("conversation_message_id"),
            "vk_date": message.get("date"),
        },
    )
