"""Telegram handler package.

Пакет собирает aiogram-роутеры по областям ответственности.
Бизнес-логика продолжает жить в application/services; здесь остаётся
только transport-specific входной слой Telegram.
"""

from aiogram import Router

from . import (
    admin, albums, diagnostics, dm, main, menu, moderation, site_feedback, site_feedback_admin,
)

router = Router()

# Журнал сообщений участников для зачистки при бане: не потребляет события.
router.message.outer_middleware(moderation.MessageLogMiddleware())

# Важен порядок: команды админки должны регистрироваться до
# широких relay/media handlers из main.py.
router.include_router(admin.router)
router.include_router(diagnostics.router)
# Модерация (/ban /mute и слова «бан»/«мут») — до широких relay-хэндлеров из main.py.
router.include_router(moderation.router)
router.include_router(site_feedback_admin.router)
# Меню раньше site_feedback и DM: кнопка `(つ✧ω✧)つ` и шаги сценариев не должны
# попасть ни в черновик отзыва, ни в обычную ретрансляцию.
router.include_router(menu.router)
router.include_router(site_feedback.router)
# Albums must intercept ALL parts before DM/main broad media handlers.
router.include_router(albums.router)
router.include_router(dm.router)
router.include_router(main.router)

__all__ = ["router"]
