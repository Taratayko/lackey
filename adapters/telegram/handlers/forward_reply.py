"""Explicit forward command in reply to ANY accessible Telegram message."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import Message

from adapters.telegram import TelegramChannelAdapter
from application.services import RelayService, RelayTextRequest
from application.services.forward_intent import parse_forward_intent
from database import db
from domain.models import ChannelRef, MessageRef
from .common import has_media, sender_mention
from presentation.formatters import escape_html

logger = logging.getLogger(__name__)


async def forward_replied_message(
    msg: Message, bot: Bot, replied: Message, *, unknown_as_plain_reply: bool = False
) -> bool:
    """True if a forward command was recognized, even when sending was refused.

    In groups, Telegram Privacy Mode may prevent the update from arriving.
    Private chats do not have that limitation.
    """
    intent = parse_forward_intent(msg.text or msg.caption)
    if intent is None:
        return False
    logger.info("Forward command received chat=%s message=%s source=%s",
                msg.chat.id, msg.message_id, replied.message_id)
    target = await db.get_repositories().chats.get_chat_by_name(intent.destination_name)
    if target is None:
        # In a normal bot dialogue "That's great!" must remain an ordinary
        # reply, not become an invalid-forward error just because of "!".
        if unknown_as_plain_reply:
            return False
        # In group chats (беседы) an unknown destination is silently ignored:
        # no "Не доставлено ... коммуна не найдена" error is posted.
        if msg.chat.type != "private":
            return False
        await msg.reply(f"Не доставлено 💔 Коммуна «{escape_html(intent.destination_name)}» не найдена.")
        return True
    if getattr(replied, "has_protected_content", False):
        await msg.reply("Не доставлено 💔 Пересылка этого сообщения запрещена защитой Telegram.")
        return True
    if has_media(msg):
        await msg.reply("Не доставлено 💔 Для пересылки ответь текстом «НазваниеЧата!» без вложения.")
        return True
    request = RelayTextRequest(
        origin_chat_id=msg.chat.id,
        origin_message_id=msg.message_id,
        origin_topic_id=msg.message_thread_id,
        target_chat=target,
        text=intent.comment,
        sender_label=sender_mention(msg),
        sender_user_id=msg.from_user.id if msg.from_user else None,
        forward_source=MessageRef(
            ChannelRef("telegram", replied.chat.id, topic_id=replied.message_thread_id),
            replied.message_id,
        ),
    )
    service = RelayService(db.get_repositories(), TelegramChannelAdapter(bot))
    result = (await service.relay_dm(request) if msg.chat.type == "private"
              else await service.relay_group(request))
    if result.user_error:
        await msg.reply(result.user_error)
    elif result.skipped and msg.chat.type == "private":
        await msg.reply("Не доставлено 💔 Пересылка из ЛС в эту коммуну запрещена правилами доступа.")
    return True
