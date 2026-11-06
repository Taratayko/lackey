"""Telegram sender/media adapter.

Этот модуль изолирует прямые вызовы aiogram для отправки текста, медиа,
реакций и простых moderation-действий. Текущие handlers пока остаются
aiogram-хэндлерами, но отправка постепенно уходит за адаптер.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    ChatPermissions,
    FSInputFile,
    Message as TelegramMessage,
    ReactionTypeEmoji,
)

from domain.models import (
    Attachment,
    AttachmentKind,
    ChannelInfo,
    ChannelRef,
    MessageRef,
    OutgoingMessage,
)
from ports.channel import ChannelPort
from .message_transfer import copy_existing_message, forward_existing_message

REPLY_NOT_FOUND_MARKER = "message to be replied not found"


def _to_optional_int(value: str | int | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _to_int(value: str | int) -> int:
    return int(value)


def _channel_ref(chat_id: str | int, topic_id: str | int | None = None) -> ChannelRef:
    return ChannelRef(channel="telegram", external_chat_id=chat_id, topic_id=topic_id)


def _message_ref(chat_id: str | int, message_id: str | int,
                 topic_id: str | int | None = None) -> MessageRef:
    return MessageRef(
        channel_ref=_channel_ref(chat_id=chat_id, topic_id=topic_id),
        external_message_id=message_id,
    )


def _sent_message_ref(sent: TelegramMessage) -> MessageRef:
    topic_id = getattr(sent, "message_thread_id", None)
    return _message_ref(sent.chat.id, sent.message_id, topic_id)


def telegram_message_to_attachments(msg: TelegramMessage) -> tuple[Attachment, ...]:
    """Преобразовать aiogram Message в доменные Attachment.

    Используется переходным слоем handlers -> ReplyService. Файлы не скачиваются:
    берём только Telegram file_id, которого достаточно для повторной отправки
    через Bot API.
    """
    if msg.photo:
        return (Attachment(kind=AttachmentKind.PHOTO, external_file_id=msg.photo[-1].file_id),)
    if msg.video:
        return (Attachment(kind=AttachmentKind.VIDEO, external_file_id=msg.video.file_id),)
    if msg.video_note:
        return (Attachment(kind=AttachmentKind.VIDEO_NOTE, external_file_id=msg.video_note.file_id),)
    if msg.document:
        return (Attachment(kind=AttachmentKind.DOCUMENT, external_file_id=msg.document.file_id),)
    if msg.audio:
        return (Attachment(kind=AttachmentKind.AUDIO, external_file_id=msg.audio.file_id),)
    if msg.voice:
        return (Attachment(kind=AttachmentKind.VOICE, external_file_id=msg.voice.file_id),)
    if msg.sticker:
        return (Attachment(kind=AttachmentKind.STICKER, external_file_id=msg.sticker.file_id),)
    if msg.animation:
        return (Attachment(kind=AttachmentKind.ANIMATION, external_file_id=msg.animation.file_id),)
    return ()


def _base_kwargs(chat_id: int, topic_id: int | None = None,
                 reply_to_message_id: int | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"chat_id": chat_id}
    if topic_id:
        kwargs["message_thread_id"] = topic_id
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    return kwargs


def _strip_reply(kwargs: dict[str, Any]) -> None:
    kwargs.pop("reply_to_message_id", None)


async def send_in_topic(
    bot: Bot,
    chat_id: int,
    text: str,
    topic_id: int | None = None,
    reply_to_message_id: int | None = None,
    disable_web_page_preview: bool = False,
) -> TelegramMessage:
    """Отправить HTML-текст в чат/топик с fallback без reply.

    Если Telegram отвечает, что reply-сообщение удалено или недоступно,
    отправляем тот же текст без reply, чтобы не потерять ретрансляцию.
    """
    kwargs = _base_kwargs(chat_id, topic_id, reply_to_message_id)
    kwargs.update({"text": text, "parse_mode": "HTML"})
    if disable_web_page_preview:
        kwargs["disable_web_page_preview"] = True

    try:
        return await bot.send_message(**kwargs)
    except TelegramBadRequest as e:
        if reply_to_message_id and REPLY_NOT_FOUND_MARKER in (e.message or ""):
            _strip_reply(kwargs)
            return await bot.send_message(**kwargs)
        raise


async def send_media_to_chat(
    bot: Bot,
    msg: TelegramMessage,
    chat_id: int,
    topic_id: int | None,
    caption: str | None = None,
    reply_to_message_id: int | None = None,
) -> TelegramMessage:
    """Отправить поддерживаемое медиа из aiogram-сообщения.

    Поддерживает фото, видео, документы, аудио, voice, video note, стикеры
    и animation/gif. Для sticker/video_note caption не передаётся, потому что
    Telegram Bot API его там не поддерживает.
    """
    kwargs = _base_kwargs(chat_id, topic_id, reply_to_message_id)
    if caption:
        kwargs["caption"] = caption
        kwargs["parse_mode"] = "HTML"

    no_caption_kwargs = {k: v for k, v in kwargs.items() if k not in ("caption", "parse_mode")}

    async def _send() -> TelegramMessage:
        if msg.photo:
            return await bot.send_photo(photo=msg.photo[-1].file_id, **kwargs)
        if msg.video:
            return await bot.send_video(video=msg.video.file_id, **kwargs)
        if msg.document:
            return await bot.send_document(document=msg.document.file_id, **kwargs)
        if msg.audio:
            return await bot.send_audio(audio=msg.audio.file_id, **kwargs)
        if msg.voice:
            return await bot.send_voice(voice=msg.voice.file_id, **kwargs)
        if msg.video_note:
            return await bot.send_video_note(video_note=msg.video_note.file_id, **no_caption_kwargs)
        if msg.sticker:
            return await bot.send_sticker(sticker=msg.sticker.file_id, **no_caption_kwargs)
        if msg.animation:
            return await bot.send_animation(animation=msg.animation.file_id, **kwargs)
        raise ValueError("Сообщение не содержит поддерживаемого медиа")

    try:
        return await _send()
    except TelegramBadRequest as e:
        if reply_to_message_id and REPLY_NOT_FOUND_MARKER in (e.message or ""):
            _strip_reply(kwargs)
            _strip_reply(no_caption_kwargs)
            return await _send()
        raise


async def set_delivered_reaction(
    bot: Bot,
    chat_id: int,
    message_id: int,
    emoji: str = "✍",
) -> None:
    """Поставить реакцию доставки. Ошибки реакции намеренно игнорируются."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        pass


async def delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    """Удалить сообщение через Telegram Bot API."""
    await bot.delete_message(chat_id=chat_id, message_id=message_id)


async def ban_chat_member(bot: Bot, chat_id: int, user_id: int) -> None:
    """Забанить участника в Telegram-чате."""
    await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)


async def unban_chat_member(bot: Bot, chat_id: int, user_id: int,
                            only_if_banned: bool = True) -> None:
    """Разбанить участника в Telegram-чате."""
    await bot.unban_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        only_if_banned=only_if_banned,
    )


async def mute_chat_member(
    bot: Bot, chat_id: int, user_id: int, until: datetime | None = None,
) -> None:
    """Запретить участнику писать (until=None — бессрочно)."""
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=until,
    )


async def unmute_chat_member(bot: Bot, chat_id: int, user_id: int) -> None:
    """Вернуть участнику права чата по умолчанию (а не «все права»)."""
    permissions = None
    try:
        chat = await bot.get_chat(chat_id)
        permissions = getattr(chat, "permissions", None)
    except Exception:
        permissions = None
    if permissions is None:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )
    await bot.restrict_chat_member(
        chat_id=chat_id, user_id=user_id, permissions=permissions,
    )


class TelegramChannelAdapter(ChannelPort):
    """Telegram transport implementation of ChannelPort."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(self, message: OutgoingMessage) -> MessageRef:
        if message.attachments:
            return await self.send_media(message)
        return await self.send_text(
            target=message.ref.channel_ref,
            text=message.text or "",
            reply_to=message.reply_to,
        )

    async def send_text(
        self,
        target: ChannelRef,
        text: str,
        reply_to: MessageRef | None = None,
        disable_web_page_preview: bool = False,
    ) -> MessageRef:
        sent = await send_in_topic(
            bot=self._bot,
            chat_id=_to_int(target.external_chat_id),
            text=text,
            topic_id=_to_optional_int(target.topic_id),
            reply_to_message_id=_to_optional_int(reply_to.message_id) if reply_to else None,
            disable_web_page_preview=disable_web_page_preview,
        )
        return _sent_message_ref(sent)

    async def send_media(self, message: OutgoingMessage) -> MessageRef:
        attachment = next(iter(message.attachments), None)
        if attachment is None:
            return await self.send_text(message.ref.channel_ref, message.text or "", message.reply_to)

        target = message.ref.channel_ref
        chat_id = _to_int(target.external_chat_id)
        topic_id = _to_optional_int(target.topic_id)
        reply_to_message_id = _to_optional_int(message.reply_to.message_id) if message.reply_to else None
        sent = await self._send_attachment(chat_id, topic_id, attachment, message.text, reply_to_message_id)
        return _sent_message_ref(sent)

    async def forward_message(self, target: ChannelRef, source: MessageRef) -> MessageRef:
        return await forward_existing_message(self._bot, target, source)

    async def copy_message(self, target: ChannelRef, source: MessageRef) -> MessageRef:
        return await copy_existing_message(self._bot, target, source)

    async def send_album(
        self, target: ChannelRef, attachments: tuple[Attachment, ...],
        caption: str | None = None, reply_to: MessageRef | None = None,
    ) -> tuple[MessageRef, ...]:
        from .albums import send_album_to_chat
        return await send_album_to_chat(
            self._bot, target, attachments, caption, reply_to, self._send_attachment,
        )

    async def send_photo_from_path(
        self,
        target: ChannelRef,
        photo_path: str | Path,
        caption: str | None = None,
    ) -> MessageRef:
        kwargs = _base_kwargs(
            chat_id=_to_int(target.external_chat_id),
            topic_id=_to_optional_int(target.topic_id),
        )
        if caption:
            kwargs["caption"] = caption
            kwargs["parse_mode"] = "HTML"
        sent = await self._bot.send_photo(photo=FSInputFile(Path(photo_path)), **kwargs)
        return _sent_message_ref(sent)

    async def delete_message(self, message_ref: MessageRef) -> None:
        await delete_message(
            self._bot,
            chat_id=_to_int(message_ref.chat_id),
            message_id=_to_int(message_ref.message_id),
        )

    async def set_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        await set_delivered_reaction(
            self._bot,
            chat_id=_to_int(message_ref.chat_id),
            message_id=_to_int(message_ref.message_id),
            emoji=emoji,
        )

    async def get_channel_info(self, target: ChannelRef) -> ChannelInfo:
        chat = await self._bot.get_chat(_to_int(target.external_chat_id))
        return ChannelInfo(
            ref=target,
            title=getattr(chat, "title", None),
            link=getattr(chat, "invite_link", None),
            meta={"raw": chat},
        )

    async def is_chat_admin(self, target: ChannelRef, user_id: str | int) -> bool:
        try:
            member = await self._bot.get_chat_member(
                chat_id=_to_int(target.external_chat_id),
                user_id=_to_int(user_id),
            )
            return member.status in ("administrator", "creator")
        except Exception:
            return False

    async def get_self_user_id(self) -> int:
        me = await self._bot.get_me()
        return int(me.id)

    async def can_restrict_members(self, target: ChannelRef, user_id: str | int) -> bool:
        try:
            member = await self._bot.get_chat_member(
                chat_id=_to_int(target.external_chat_id),
                user_id=_to_int(user_id),
            )
        except Exception:
            return False
        if member.status == "creator":
            return True
        return member.status == "administrator" and bool(
            getattr(member, "can_restrict_members", False)
        )

    async def mute_user(self, target: ChannelRef, user_id: str | int,
                        until: datetime | None = None) -> None:
        await mute_chat_member(
            self._bot, _to_int(target.external_chat_id), _to_int(user_id), until,
        )

    async def unmute_user(self, target: ChannelRef, user_id: str | int) -> None:
        await unmute_chat_member(
            self._bot, _to_int(target.external_chat_id), _to_int(user_id),
        )

    async def ban_user(self, target: ChannelRef, user_id: str | int) -> None:
        await ban_chat_member(self._bot, _to_int(target.external_chat_id), _to_int(user_id))

    async def unban_user(self, target: ChannelRef, user_id: str | int,
                         only_if_banned: bool = True) -> None:
        await unban_chat_member(
            self._bot,
            _to_int(target.external_chat_id),
            _to_int(user_id),
            only_if_banned=only_if_banned,
        )

    async def approve_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        await self._bot.approve_chat_join_request(
            chat_id=_to_int(target.external_chat_id),
            user_id=_to_int(user_id),
        )

    async def decline_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        await self._bot.decline_chat_join_request(
            chat_id=_to_int(target.external_chat_id),
            user_id=_to_int(user_id),
        )

    async def _send_attachment(
        self,
        chat_id: int,
        topic_id: int | None,
        attachment: Attachment,
        caption: str | None,
        reply_to_message_id: int | None,
    ) -> TelegramMessage:
        file_id = attachment.external_file_id
        if not file_id:
            raise ValueError("Telegram attachment requires external_file_id")

        kwargs = _base_kwargs(chat_id, topic_id, reply_to_message_id)
        if caption and attachment.kind not in (AttachmentKind.STICKER, AttachmentKind.VIDEO_NOTE):
            kwargs["caption"] = caption
            kwargs["parse_mode"] = "HTML"

        no_caption_kwargs = {k: v for k, v in kwargs.items() if k not in ("caption", "parse_mode")}
        try:
            return await self._send_attachment_once(attachment.kind, file_id, kwargs, no_caption_kwargs)
        except TelegramBadRequest as e:
            if reply_to_message_id and REPLY_NOT_FOUND_MARKER in (e.message or ""):
                _strip_reply(kwargs)
                _strip_reply(no_caption_kwargs)
                return await self._send_attachment_once(attachment.kind, file_id, kwargs, no_caption_kwargs)
            raise

    async def _send_attachment_once(
        self,
        kind: AttachmentKind,
        file_id: str,
        kwargs: dict[str, Any],
        no_caption_kwargs: dict[str, Any],
    ) -> TelegramMessage:
        if kind == AttachmentKind.PHOTO:
            return await self._bot.send_photo(photo=file_id, **kwargs)
        if kind == AttachmentKind.VIDEO:
            return await self._bot.send_video(video=file_id, **kwargs)
        if kind == AttachmentKind.VIDEO_NOTE:
            return await self._bot.send_video_note(video_note=file_id, **no_caption_kwargs)
        if kind == AttachmentKind.DOCUMENT:
            return await self._bot.send_document(document=file_id, **kwargs)
        if kind == AttachmentKind.AUDIO:
            return await self._bot.send_audio(audio=file_id, **kwargs)
        if kind == AttachmentKind.VOICE:
            return await self._bot.send_voice(voice=file_id, **kwargs)
        if kind == AttachmentKind.STICKER:
            return await self._bot.send_sticker(sticker=file_id, **no_caption_kwargs)
        if kind == AttachmentKind.ANIMATION:
            return await self._bot.send_animation(animation=file_id, **kwargs)
        raise ValueError(f"Unsupported Telegram attachment kind: {kind.value}")
