"""Telegram group-media input; all delivery rules are in RelayService."""
from aiogram import Bot
from aiogram.types import Message

from adapters.telegram import TelegramChannelAdapter, telegram_message_to_attachments
from application.services import RelayService, RelayTextRequest
from database import db
from .common import (
    extract_target_only, is_real_reply, resolve_chat_and_text, resolve_target_only,
    sender_mention, text_as_written,
)


async def relay_group_media(msg: Message, bot: Bot):
    if msg.chat.type == "private" or is_real_reply(msg):
        return
    source = msg.caption or msg.text or ""
    only_name = extract_target_only(source)
    target = await resolve_target_only(source) if only_name else None
    extra = text_as_written(source) if target else ""
    if not target:
        parsed = await resolve_chat_and_text(source)
        if parsed:
            target, extra = parsed
    if not target:
        return
    service = RelayService(db.get_repositories(), TelegramChannelAdapter(bot))
    result = await service.relay_group(RelayTextRequest(
        origin_chat_id=msg.chat.id,
        origin_message_id=msg.message_id,
        origin_topic_id=msg.message_thread_id,
        target_chat=target,
        text=extra,
        sender_label=sender_mention(msg),
        sender_user_id=msg.from_user.id if msg.from_user else None,
        attachments=telegram_message_to_attachments(msg),
    ))
    if result.user_error:
        await msg.reply(result.user_error)
