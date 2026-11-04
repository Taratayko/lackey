"""Telegram handlers for runtime diagnostics."""
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from application.services import DiagnosticsService
from config import settings
from database import db

router = Router()


def _diagnostics_service() -> DiagnosticsService:
    """Создать DiagnosticsService для текущего runtime."""
    return DiagnosticsService(database=db, owner_user_id=settings.ADMIN_ID)


def _actor_user_id(msg: Message) -> int | None:
    return msg.from_user.id if msg.from_user else None


@router.message(Command("diag"))
@router.message(Command("health"))
async def cmd_diag(msg: Message, bot: Bot) -> None:
    """Owner-only health-check бота, Mongo и базовых коллекций."""
    bot_username: str | None = None
    try:
        me = await bot.get_me()
        bot_username = me.username
    except Exception:
        bot_username = None

    result = await _diagnostics_service().render_health_text(
        _actor_user_id(msg),
        current_chat_id=msg.chat.id,
        bot_username=bot_username,
    )
    if result and result.text:
        await msg.reply(result.text, disable_web_page_preview=True)
