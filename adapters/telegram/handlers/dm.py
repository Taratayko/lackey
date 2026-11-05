"""Telegram handlers for private messages / DM relay."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from adapters.telegram import (
    TelegramChannelAdapter,
    telegram_message_to_attachments,
)
from application.services import RelayService, RelayTextRequest
from database import db
from presentation.formatters import escape_html
from adapters.telegram.menu_keyboards import reply_menu_keyboard
from presentation.texts import DM_FORMAT_HINT, DM_WELCOME, DM_WELCOME_BACK

from .admin import build_full_list_text
from .common import (
    extract_target_and_text,
    extract_target_only,
    has_media,
    is_real_reply,
    resolve_chat_and_text,
    resolve_target_only,
    send_rich,
    sender_mention,
)

router = Router()
logger = logging.getLogger(__name__)


def is_not_real_reply(msg: Message) -> bool:
    """True для обычных ЛС-сообщений, не являющихся reply-цепочкой.

    После выноса DM-router он подключается раньше main-router. Поэтому ЛС reply
    на сообщение бота нужно пропускать мимо DM relay, чтобы его обработал
    ReplyService в main-router.
    """
    return not is_real_reply(msg)


async def _send_dm_welcome(msg: Message) -> None:
    """Отправить приветствие + полный список коммун новому пользователю."""
    await send_rich(msg, DM_WELCOME, reply_markup=reply_menu_keyboard())
    full_list = await build_full_list_text()
    await send_rich(msg, full_list)


@router.message(Command("start"))
async def cmd_start(msg: Message) -> None:
    """Приветствие при первом старте в ЛС."""
    if msg.chat.type != "private":
        return
    is_new = await db.is_dm_user_new(msg.from_user.id)
    payload = (msg.text or "").split(maxsplit=1)
    if len(payload) == 2 and payload[1].strip().lower() == "communesite":
        from .site_feedback import command_site
        await command_site(msg)
        return
    if is_new:
        await _send_dm_welcome(msg)
    else:
        await send_rich(msg, DM_WELCOME_BACK, reply_markup=reply_menu_keyboard())


@router.message(Command("anon"))
async def cmd_anon(msg: Message) -> None:
    if msg.chat.type != "private":
        return
    await db.set_dm_session(msg.from_user.id, anonymous=True)
    await msg.reply("Теперь ваши сообщения анонимны 🥷")


@router.message(Command("deanon"))
async def cmd_deanon(msg: Message) -> None:
    if msg.chat.type != "private":
        return
    await db.set_dm_session(msg.from_user.id, anonymous=False)
    await msg.reply("Теперь ваши сообщения не анонимны 📛")


@router.message(F.chat.type == "private", F.text & ~F.text.startswith("/"), is_not_real_reply)
async def handle_dm_message(msg: Message, bot: Bot) -> None:
    """Обработка текстовых сообщений из ЛС."""
    source = msg.text or ""
    resolved = await resolve_chat_and_text(source)

    if not resolved:
        parsed = extract_target_and_text(source)
        if not parsed:
            await msg.reply(DM_FORMAT_HINT)
        else:
            target_name_err = parsed[0]
            await msg.reply(
                f"Не доставлено 💔\nОшибка: коммуна «{escape_html(target_name_err)}» не найдена\n"
                "Посмотри список: /listmsk, /listspb или /listreg"
            )
        return

    target_chat, text = resolved
    await _send_dm_relay(bot, msg, target_chat, text)


@router.message(F.chat.type == "private", has_media, is_not_real_reply)
async def handle_dm_media_message(msg: Message, bot: Bot) -> None:
    """Обработка медиа-сообщений из ЛС."""
    source = msg.caption or msg.text or ""
    target_chat = None
    extra_text = ""

    only_name = extract_target_only(source) if source else None
    if only_name:
        target_chat = await db.get_chat_by_name(only_name)
        if not target_chat:
            target_chat = await resolve_target_only(source)

    if not target_chat:
        resolved = await resolve_chat_and_text(source)
        if resolved:
            target_chat, extra_text = resolved

    if not target_chat:
        if source:
            parsed = extract_target_and_text(source)
            if parsed:
                await msg.reply(
                    f"Не доставлено 💔\nОшибка: коммуна «{escape_html(parsed[0])}» не найдена\n"
                    "Посмотри список: /listmsk, /listspb или /listreg"
                )
        return

    await _send_dm_media_relay(bot, msg, target_chat, extra_text)


async def _send_dm_relay(bot: Bot, msg: Message, target_chat: dict, text: str) -> None:
    """Отправляет текст из ЛС через RelayService."""
    service = RelayService(
        repositories=db.get_repositories(),
        channel=TelegramChannelAdapter(bot),
    )
    result = await service.relay_dm_text(
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


async def _send_dm_media_relay(bot: Bot, msg: Message, target_chat: dict, extra_text: str = "") -> None:
    """DM media travels through the same privacy-aware pipeline as DM text."""
    service = RelayService(db.get_repositories(), TelegramChannelAdapter(bot))
    result = await service.relay_dm(RelayTextRequest(
        origin_chat_id=msg.chat.id,
        origin_message_id=msg.message_id,
        origin_topic_id=msg.message_thread_id,
        target_chat=target_chat,
        text=extra_text,
        sender_label=sender_mention(msg),
        sender_user_id=msg.from_user.id if msg.from_user else None,
        attachments=telegram_message_to_attachments(msg),
    ))
    if result.user_error:
        await msg.reply(result.user_error)
