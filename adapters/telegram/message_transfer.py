"""Forward/copy of Telegram messages; copy hides original attribution."""
from __future__ import annotations

from aiogram import Bot

from domain.models import ChannelRef, MessageRef


def _kwargs(target: ChannelRef) -> dict:
    result = {"chat_id": int(target.chat_id)}
    if target.topic_id:
        result["message_thread_id"] = int(target.topic_id)
    return result


async def forward_existing_message(bot: Bot, target: ChannelRef, source: MessageRef) -> MessageRef:
    sent = await bot.forward_message(
        from_chat_id=int(source.chat_id), message_id=int(source.message_id),
        **_kwargs(target),
    )
    return MessageRef(
        ChannelRef("telegram", sent.chat.id, topic_id=sent.message_thread_id),
        sent.message_id,
    )


async def copy_existing_message(bot: Bot, target: ChannelRef, source: MessageRef) -> MessageRef:
    # Bot API copyMessage returns MessageId, not Message. The destination is known.
    sent_id = await bot.copy_message(
        from_chat_id=int(source.chat_id), message_id=int(source.message_id),
        **_kwargs(target),
    )
    return MessageRef(target, sent_id.message_id)
