# handlers.py
"""
Все обработчики сообщений.

Логика:
  1. Если сообщение является reply на бот-сообщение → relay_reply()
  2. Если сообщение начинается с имени известного чата → relay_message()
  3. Команды /list, /listmsk, /listspb → list_chats()
  4. Команды администратора → admin_*()
"""
import logging

from aiogram import Bot, Router, F
from aiogram.types import Message

from database import db
from application.services.identity import sender_label_for_chat
from adapters.telegram import (
    TelegramChannelAdapter,
    telegram_message_to_attachments,
)
from application.services import (
    RelayService,
    RelayTextRequest,
    ReplyRelayRequest,
    ReplyService,
)
from .group_media import relay_group_media
from .forward_reply import forward_replied_message
from application.services.forward_intent import ReplyAction, decide_reply_action
from .common import (
    has_media,
    is_real_reply,
    resolve_chat_and_text,
    sender_mention,
)

router = Router()
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Хендлер reply на бот-сообщение (цепочка диалога)
# ═══════════════════════════════════════════════════════════════════════════════

async def _do_relay_reply(
    bot: Bot, msg: Message, replied: Message, text: str | None = None,
    attachments=None, current_message_ids: tuple[int, ...] = (),
):
    """
    Пробрасывает reply через ReplyService.

    handlers.py пока остаётся aiogram-входом: здесь проверяем, что reply был
    именно на сообщение бота, и преобразуем Telegram media в доменные
    attachments. Поиск thread-mapping и отправка в противоположную сторону
    вынесены в application/services/reply.py.
    """
    me = await bot.get_me()
    if not replied.from_user or replied.from_user.id != me.id:
        return

    repositories = db.get_repositories()
    # A private /anon reply must not reveal @username or full name.
    sender_label = await sender_label_for_chat(
        repositories.dm,
        msg.chat.type,
        msg.from_user.id if msg.from_user else None,
        sender_mention(msg),
    )
    service = ReplyService(repositories=repositories, channel=TelegramChannelAdapter(bot))
    result = await service.relay_reply(
        ReplyRelayRequest(
            current_chat_id=msg.chat.id,
            current_message_id=msg.message_id,
            replied_message_id=replied.message_id,
            sender_label=sender_label,
            text=text,
            attachments=(telegram_message_to_attachments(msg) if attachments is None else attachments),
            current_topic_id=msg.message_thread_id,
            current_message_ids=current_message_ids,
            captionless_media=bool(msg.sticker or msg.video_note),
        )
    )
    if result.user_error:
        await msg.reply(result.user_error)


@router.message(F.text & ~F.text.startswith("/"), is_real_reply)
async def handle_reply(msg: Message, bot: Bot):
    """An explicit destination forwards; otherwise reply to bot starts a thread."""
    replied = msg.reply_to_message
    if not replied:
        return
    me = await bot.get_me()
    replied_by_bot = bool(replied.from_user and replied.from_user.id == me.id)
    action = decide_reply_action(msg.text, replied_by_bot)
    if action == ReplyAction.FORWARD:
        handled = await forward_replied_message(
            msg, bot, replied, unknown_as_plain_reply=replied_by_bot
        )
        if not handled and replied_by_bot:
            await _do_relay_reply(bot, msg, replied, text=msg.text)
    elif action == ReplyAction.REPLY:
        await _do_relay_reply(bot, msg, replied, text=msg.text)


@router.message(~F.text.startswith("/"), is_real_reply)
async def handle_reply_media(msg: Message, bot: Bot):
    """Media reply to bot; a routed caption reports unsupported mixed format."""
    if not has_media(msg):
        return
    replied = msg.reply_to_message
    if not replied:
        return
    me = await bot.get_me()
    replied_by_bot = bool(replied.from_user and replied.from_user.id == me.id)
    action = decide_reply_action(msg.caption, replied_by_bot)
    if action == ReplyAction.FORWARD:
        handled = await forward_replied_message(
            msg, bot, replied, unknown_as_plain_reply=replied_by_bot
        )
        if not handled and replied_by_bot:
            await _do_relay_reply(bot, msg, replied, text=msg.caption)
    elif action == ReplyAction.REPLY:
        await _do_relay_reply(bot, msg, replied, text=msg.caption)


# ═══════════════════════════════════════════════════════════════════════════════
#  Основная ретрансляция (текст + медиа)
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text & ~F.text.startswith("/"))
async def handle_message(msg: Message, bot: Bot):
    """Разбираем «ЦелевойЧат! текст» и пересылаем (текст)."""

    # ЛС обрабатывает отдельный dm-router.
    if msg.chat.type == "private":
        return

    # Пропускаем настоящие reply — обрабатывается handle_reply.
    if msg.reply_to_message:
        rtm = msg.reply_to_message
        is_topic_header = bool(
            getattr(rtm, "forum_topic_created", None) or
            getattr(rtm, "forum_topic_edited", None) or
            (msg.message_thread_id and rtm.message_id == msg.message_thread_id)
        )
        if not is_topic_header:
            return

    resolved = await resolve_chat_and_text(msg.text)
    logger.info(f"handle_message: chat={msg.chat.id} resolved={resolved is not None}")

    if not resolved:
        return

    target_chat, text = resolved
    await _do_relay_text(bot, msg, target_chat, text)


@router.message(has_media)
async def handle_media_message(msg: Message, bot: Bot):
    """Routed media entrypoint; business path lives in group_media."""
    await relay_group_media(msg, bot)


async def _do_relay_text(bot: Bot, msg: Message, target_chat: dict, text: str):
    """Отправляет текстовое сообщение через RelayService."""
    service = RelayService(
        repositories=db.get_repositories(),
        channel=TelegramChannelAdapter(bot),
    )
    result = await service.relay_group_text(
        RelayTextRequest(
            origin_chat_id=msg.chat.id,
            origin_message_id=msg.message_id,
            origin_topic_id=msg.message_thread_id,
            target_chat=target_chat,
            text=text,
            sender_label=sender_mention(msg),
            sender_user_id=msg.from_user.id if msg.from_user else None,
        )
    )
    if result.user_error:
        await msg.reply(result.user_error)

