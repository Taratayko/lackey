"""Telegram handlers for moderation commands."""

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from adapters.telegram import TelegramChannelAdapter
from application.services import ModerationRequest, ModerationService, ModerationTarget
from database import db

router = Router()


def _moderation_service(bot: Bot) -> ModerationService:
    """Создать сервис модерации для текущего Telegram runtime."""
    return ModerationService(
        repositories=db.get_repositories(),
        channel=TelegramChannelAdapter(bot),
    )


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Проверить, является ли пользователь админом чата."""
    return await _moderation_service(bot).is_chat_admin(chat_id, user_id)


async def execute_ban(bot: Bot, msg: Message, target_user_id: int, target_username: str | None) -> bool:
    """Бан в текущем чате через ModerationService."""
    if not msg.from_user:
        return False

    result = await _moderation_service(bot).ban(
        ModerationRequest(
            chat_id=msg.chat.id,
            actor_user_id=msg.from_user.id,
            target=ModerationTarget(target_user_id, target_username),
        )
    )
    if result.user_message:
        await msg.reply(result.user_message)
    return result.success


async def execute_megaban(bot: Bot, msg: Message, target_user_id: int, target_username: str | None) -> None:
    """Бан во всех зарегистрированных чатах через ModerationService."""
    if not msg.from_user:
        return

    result = await _moderation_service(bot).megaban(
        ModerationRequest(
            chat_id=msg.chat.id,
            actor_user_id=msg.from_user.id,
            target=ModerationTarget(target_user_id, target_username),
        )
    )
    if result.user_message:
        await msg.reply(result.user_message)


async def _unban_by_username(bot: Bot, msg: Message, username: str) -> None:
    """Разбанить username в текущем чате через ModerationService."""
    if not msg.from_user:
        return

    result = await _moderation_service(bot).unban_by_username(
        chat_id=msg.chat.id,
        actor_user_id=msg.from_user.id,
        username=username,
    )
    if result.user_message:
        await msg.reply(result.user_message)


@router.message(Command("ban"))
async def cmd_ban(msg: Message, bot: Bot):
    if msg.chat.type == "private":
        return
    if not msg.reply_to_message:
        await msg.reply("Используй reply на сообщение нарушителя")
        return
    target = msg.reply_to_message.from_user
    if target:
        await execute_ban(bot, msg, target.id, target.username)


@router.message(Command("megaban"))
async def cmd_megaban(msg: Message, bot: Bot):
    if msg.chat.type == "private":
        return
    if not msg.reply_to_message:
        await msg.reply("Используй reply на сообщение нарушителя")
        return
    target = msg.reply_to_message.from_user
    if target:
        await execute_megaban(bot, msg, target.id, target.username)


@router.message(Command("unban"))
async def cmd_unban(msg: Message, bot: Bot):
    if msg.chat.type == "private":
        return
    parts = msg.text.split(maxsplit=1) if msg.text else []
    if len(parts) < 2:
        await msg.reply("Использование: /unban @username")
        return
    await _unban_by_username(bot, msg, parts[1].strip())


@router.message(Command("banlist"))
async def cmd_banlist(msg: Message, bot: Bot):
    if msg.chat.type == "private":
        return
    if not msg.from_user:
        return

    result = await _moderation_service(bot).banlist_text(
        chat_id=msg.chat.id,
        actor_user_id=msg.from_user.id,
    )
    if result.user_message:
        await msg.reply(result.user_message)


@router.message(F.text & F.chat.type != "private", lambda m: bool(m.text and m.text.strip().lower().split()[0] in ("бан", "мегабан", "разбан") if m.text else False))
async def handle_text_ban(msg: Message, bot: Bot):
    if not msg.text:
        return
    first = msg.text.strip().lower().split()[0]
    if first not in ("бан", "мегабан", "разбан"):
        return

    if first == "бан":
        if not msg.reply_to_message:
            await msg.reply("Используй reply на сообщение нарушителя")
            return
        target = msg.reply_to_message.from_user
        if target:
            await execute_ban(bot, msg, target.id, target.username)

    elif first == "мегабан":
        if not msg.reply_to_message:
            await msg.reply("Используй reply на сообщение нарушителя")
            return
        target = msg.reply_to_message.from_user
        if target:
            await execute_megaban(bot, msg, target.id, target.username)

    elif first == "разбан":
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            await msg.reply("Использование: Разбан @username")
            return
        await _unban_by_username(bot, msg, parts[1].strip())
