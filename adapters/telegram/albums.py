"""Telegram media-group sender; retains refs even after partial batch delivery."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo

from application.services.delivery import PartialDispatchError
from domain.models import Attachment, AttachmentKind, ChannelRef, MessageRef

MEDIA_CLASSES = {
    AttachmentKind.PHOTO: InputMediaPhoto,
    AttachmentKind.VIDEO: InputMediaVideo,
    AttachmentKind.AUDIO: InputMediaAudio,
    AttachmentKind.DOCUMENT: InputMediaDocument,
}


def _category(att: Attachment) -> str | None:
    if att.kind in (AttachmentKind.PHOTO, AttachmentKind.VIDEO):
        return "visual"
    if att.kind == AttachmentKind.AUDIO:
        return "audio"
    if att.kind == AttachmentKind.DOCUMENT:
        return "document"
    return None


def _ref(sent: Any) -> MessageRef:
    return MessageRef(
        ChannelRef("telegram", sent.chat.id, topic_id=getattr(sent, "message_thread_id", None)),
        sent.message_id,
    )


async def send_album_to_chat(
    bot: Bot,
    target: ChannelRef,
    attachments: tuple[Attachment, ...],
    caption: str | None,
    reply_to: MessageRef | None,
    send_one: Callable[[int, int | None, Attachment, str | None, int | None], Awaitable[Any]],
) -> tuple[MessageRef, ...]:
    if not attachments:
        return ()
    sent: list[MessageRef] = []
    chat_id = int(target.chat_id)
    topic_id = int(target.topic_id) if target.topic_id else None
    reply_id = int(reply_to.message_id) if reply_to else None

    # Preserve order. Telegram permits 2..10 photo+video, audio-only or doc-only.
    groups: list[list[Attachment]] = []
    for att in attachments:
        kind = _category(att)
        if groups and kind is not None and kind == _category(groups[-1][0]) and len(groups[-1]) < 10:
            groups[-1].append(att)
        else:
            groups.append([att])
    try:
        for group in groups:
            group_caption = caption if not sent else None
            if len(group) == 1 or _category(group[0]) is None:
                for idx, attachment in enumerate(group):
                    one = await send_one(chat_id, topic_id, attachment,
                                         group_caption if idx == 0 else None, reply_id)
                    sent.append(_ref(one))
                continue
            media = []
            for idx, attachment in enumerate(group):
                kwargs = {"media": attachment.external_file_id}
                if idx == 0 and group_caption:
                    kwargs.update(caption=group_caption, parse_mode="HTML")
                media.append(MEDIA_CLASSES[attachment.kind](**kwargs))
            kwargs = {"chat_id": chat_id, "media": media}
            if topic_id:
                kwargs["message_thread_id"] = topic_id
            if reply_id:
                kwargs["reply_to_message_id"] = reply_id
            try:
                produced = await bot.send_media_group(**kwargs)
            except TelegramBadRequest as exc:
                # A missing reply target is a definitive no-send: safe to retry
                # without reply. Never retry a generic/network failure.
                if reply_id and "message to be replied not found" in (exc.message or "").lower():
                    kwargs.pop("reply_to_message_id", None)
                    produced = await bot.send_media_group(**kwargs)
                else:
                    raise
            sent.extend(_ref(one) for one in produced)
        return tuple(sent)
    except Exception as exc:
        if sent:
            raise PartialDispatchError(tuple(sent), exc) from exc
        raise
