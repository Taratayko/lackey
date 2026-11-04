"""
Система онбординга для новых участников.

После Patch 08 файл остаётся тонким aiogram-router слоем. Основная логика
капчи/сессий/approve/decline вынесена в application/services/captcha.py.

Коллекции MongoDB остаются прежними:
- captcha_sessions
- captcha_chats
"""
from __future__ import annotations

from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import ChatJoinRequest, Message

from adapters.telegram import TelegramChannelAdapter
from application.services import (
    CaptchaAnswerRequest,
    CaptchaJoinRequest,
    CaptchaService,
    CaptchaSettings,
)
from config import settings
from database import db

captcha_router = Router()

# Путь к картинке для онбординга
HELLO_IMAGE_PATH = Path(__file__).parent / "hello.png"


# ──────────────────────────────────────────────────────────────────────────────
#  Service factory
# ──────────────────────────────────────────────────────────────────────────────

def _captcha_settings() -> CaptchaSettings:
    return CaptchaSettings(
        timeout_hours=settings.ONBOARDING_TIMEOUT_HOURS,
        timeout_minutes=settings.ONBOARDING_TIMEOUT_MINUTES,
        approve_delay_seconds=settings.APPROVE_DELAY_SECONDS,
        onboarding_text=settings.ONBOARDING_TEXT,
        hello_image_path=HELLO_IMAGE_PATH,
    )


def _captcha_service(bot: Bot | None = None) -> CaptchaService:
    return CaptchaService(
        repositories=db.get_repositories(),
        settings=_captcha_settings(),
        channel=TelegramChannelAdapter(bot) if bot else None,
    )


def _is_captcha_admin(msg: Message) -> bool:
    return bool(msg.from_user and msg.from_user.id == settings.ADMIN_ID)


# ──────────────────────────────────────────────────────────────────────────────
#  Публичная функция: есть ли активная сессия у пользователя?
#  Используется в handlers.py чтобы не перехватывать сообщения онбординга.
# ──────────────────────────────────────────────────────────────────────────────

async def user_has_pending_captcha(user_id: int) -> bool:
    return await _captcha_service().user_has_pending_captcha(user_id)


# ──────────────────────────────────────────────────────────────────────────────
#  Обработчик заявок на вступление
# ──────────────────────────────────────────────────────────────────────────────

@captcha_router.chat_join_request()
async def handle_join_request(event: ChatJoinRequest, bot: Bot):
    await _captcha_service(bot).handle_join_request(
        CaptchaJoinRequest(
            chat_id=event.chat.id,
            chat_title=event.chat.title or str(event.chat.id),
            user_id=event.from_user.id,
            username=event.from_user.username,
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Обработчик ответа пользователя в ЛС
#  Стоит ПЕРВЫМ в captcha_router — перехватывает ответ раньше handle_dm_message
# ──────────────────────────────────────────────────────────────────────────────

async def _has_active_captcha(msg: Message) -> bool:
    """Фильтр: True только если у пользователя есть активная незавершённая сессия."""
    if not msg.from_user:
        return False
    return await _captcha_service().should_handle_private_answer(msg.from_user.id, msg.text)


@captcha_router.message(F.chat.type == "private", F.text, _has_active_captcha)
async def handle_onboarding_answer(msg: Message, bot: Bot):
    if not msg.from_user or not msg.text:
        return

    await _captcha_service(bot).handle_onboarding_answer(
        CaptchaAnswerRequest(
            user_id=msg.from_user.id,
            username=msg.from_user.username,
            text=msg.text,
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Команды управления онбордингом (только ADMIN_ID)
# ──────────────────────────────────────────────────────────────────────────────

@captcha_router.message(Command("captcha_add"))
async def cmd_captcha_add(msg: Message):
    """
    /captcha_add — включить онбординг для текущей беседы.
    Отправлять из нужной беседы (как /addalias и другие).
    """
    if not _is_captcha_admin(msg):
        return

    result = await _captcha_service().add_captcha_chat(
        chat_id=msg.chat.id,
        actor_user_id=msg.from_user.id,
        private_chat=msg.chat.type == "private",
    )
    if result.user_message:
        await msg.reply(result.user_message)


@captcha_router.message(Command("captcha_remove"))
async def cmd_captcha_remove(msg: Message):
    """
    /captcha_remove — отключить онбординг для текущей беседы.
    Отправлять из нужной беседы (как /addalias и другие).
    """
    if not _is_captcha_admin(msg):
        return

    result = await _captcha_service().remove_captcha_chat(
        chat_id=msg.chat.id,
        private_chat=msg.chat.type == "private",
    )
    if result.user_message:
        await msg.reply(result.user_message)


@captcha_router.message(Command("captcha_list"))
async def cmd_captcha_list(msg: Message):
    """
    /captcha_list — список бесед с включённым онбордингом (только ADMIN_ID).
    """
    if not _is_captcha_admin(msg):
        return

    await msg.reply(await _captcha_service().captcha_list_text())


@captcha_router.message(Command("captcha_timeout"))
async def cmd_captcha_timeout(msg: Message):
    """
    /captcha_timeout — показать текущий таймаут (только ADMIN_ID).
    """
    if not _is_captcha_admin(msg):
        return

    await msg.reply(_captcha_service().timeout_text(), parse_mode="HTML")


# ──────────────────────────────────────────────────────────────────────────────
#  Инициализация индексов
# ──────────────────────────────────────────────────────────────────────────────

async def init_captcha_indexes(db_instance):
    """Создать индексы для коллекций капчи. Вызвать после db.connect()."""
    await db_instance._db.captcha_sessions.create_index("user_id")
    await db_instance._db.captcha_sessions.create_index("username")
    await db_instance._db.captcha_sessions.create_index("chat_id")
    await db_instance._db.captcha_sessions.create_index("status")
    await db_instance._db.captcha_sessions.create_index("expires_at")
    await db_instance._db.captcha_chats.create_index("chat_id", unique=True)
