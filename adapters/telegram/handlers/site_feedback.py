"""Private Telegram UX for site feedback and new-page proposals."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, Message

from application.services.site_feedback import SiteFeedbackError, SiteFeedbackService
from config import settings
from database import db
from domain.site_feedback import SitePage, site_url
from .site_feedback_ui import (
    confirm_menu, confirm_text, context_menu, item_text,
    main_menu, my_list_menu, page_menu, type_menu,
)

router = Router()
logger = logging.getLogger(__name__)


def service():
    return SiteFeedbackService(db.get_site_feedback_repository(), settings.ADMIN_ID)


async def record_identity(user):
    if user:
        await db.get_site_feedback_repository().update_user(
            user.id, user.username, user.full_name or "",
        )


async def show(event, text: str, markup=None):
    """Callback answers use a fresh message, leaving prior buttons harmless."""
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("communesite", ignore_case=True))
async def command_site(msg: Message):
    if msg.chat.type != "private":
        await msg.reply("Меню отзывов доступно только в ЛС бота: /communesite")
        return
    await record_identity(msg.from_user)
    await msg.answer("🏛 <b>Сайт коммун — обратная связь</b>\nВыберите действие:", reply_markup=main_menu())


@router.message(Command("cancel"))
async def command_cancel(msg: Message):
    if msg.chat.type != "private":
        return
    repo = db.get_site_feedback_repository()
    if await repo.draft(msg.from_user.id):
        await repo.clear_draft(msg.from_user.id)
        await msg.answer("Черновик удалён. /communesite")
    else:
        await msg.answer("Активного черновика нет.")


@router.callback_query(F.data.startswith("sf:"))
async def site_callback(call: CallbackQuery):
    if not call.message or call.message.chat.type != "private":
        await call.answer("Откройте ЛС с ботом", show_alert=True)
        return
    await call.answer()
    user = call.from_user
    repo = db.get_site_feedback_repository()
    svc = service()
    data = (call.data or "").split(":")
    action = data[1] if len(data) > 1 else ""
    try:
        await record_identity(user)
        if action in {"menu", "cancel"}:
            if action == "cancel":
                await repo.clear_draft(user.id)
            await show(call, "🏛 <b>Сайт коммун</b>", main_menu())
        elif action == "review":
            await svc.begin(user.id, "page_feedback")
            pages = await repo.pages()
            await show(call, "Выберите раздел сайта:", page_menu(pages))
        elif action == "new":
            await svc.begin(user.id, "new_page")
            await show(call, "Название новой страницы (4–100 символов). Напишите отдельным сообщением.\n/cancel — отмена.")
        elif action == "page" and len(data) == 3:
            draft = await svc.select_page(user.id, data[2])
            if draft["stage"] == "context":
                await show(call, "Укажите slug конкретной коммуны/мероприятия латиницей "
                           "или нажмите «Без конкретной страницы». Это необязательный шаг.", context_menu())
            else:
                await show(call, await page_description(repo, data[2]), type_menu())
        elif action == "skip":
            draft = await svc.set_context(user.id, None)
            await show(call, await page_description(repo, draft["page_key"], draft.get("context_slug")), type_menu())
        elif action == "type" and len(data) == 3:
            await svc.set_feedback_type(user.id, data[2])
            await show(call, "Напишите текст отзыва (10–2000 символов).\n/cancel — отмена.")
        elif action == "submit":
            draft = await repo.draft(user.id)
            if not draft:
                raise SiteFeedbackError("Черновик не найден; возможно, отзыв уже отправлен.")
            item = await svc.submit(user, draft)
            await show(call, f"✅ Обращение <code>{str(item['_id'])}</code> сохранено.\n"
                       "Статус: новое. Откройте «Мои предложения», чтобы следить за ним.", main_menu())
        elif action == "mine" and len(data) == 3:
            offset = bounded_offset(data[2])
            items, total = await svc.my_items(user.id, offset)
            await show(call, f"📋 Мои предложения: {total}", my_list_menu(items, offset, total))
        elif action == "item" and len(data) == 3:
            item = await svc.my_item(user.id, data[2])
            await show(call, item_text(item), my_list_menu([], 0, 0))
        else:
            await show(call, "Кнопка устарела. Откройте /communesite")
    except (SiteFeedbackError, ValueError) as exc:
        await show(call, "⚠️ " + str(exc))
    except Exception:
        logger.exception("Site feedback callback failed")
        await show(call, "Не удалось обработать запрос. Попробуйте позже; отправленное не дублируйте.")


async def page_description(repo, key: str, slug: str | None = None) -> str:
    page = await repo.page(key)
    if not page:
        raise SiteFeedbackError("Страница скрыта администратором.")
    route = page["route"]
    base = settings.SITE_FEEDBACK_BASE_URL.rstrip("/")
    from html import escape
    if "{slug}" in route and not slug:
        url = base + "/" if "commune" in key else base + "/events/"
        info = "Это шаблон страницы. Конкретный адрес зависит от slug."
    else:
        url = site_url(base, SitePage(key, page["title"], route), slug)
        info = ""
    return (f"Раздел: <b>{escape(page['title'])}</b>\n"
            f"Посмотреть: {escape(url)}\n{info}\n\nВыберите категорию отзыва:")


def bounded_offset(raw: str) -> int:
    if not raw.isdecimal() or len(raw) > 6:
        raise SiteFeedbackError("Неверная страница списка.")
    return min(int(raw), 100000)


class HasFeedbackDraft(Filter):
    """Filter checks Mongo before interception so normal DM relay remains untouched."""
    async def __call__(self, msg: Message) -> bool:
        if msg.chat.type != "private" or not msg.text or msg.text.startswith("/"):
            return False
        draft = await db.get_site_feedback_repository().draft(msg.from_user.id)
        return bool(draft and draft.get("stage") in {"context", "body", "new_title", "new_body"})


@router.message(F.chat.type == "private", F.text, HasFeedbackDraft())
async def site_draft_text(msg: Message):
    svc = service()
    try:
        repo = db.get_site_feedback_repository()
        draft = await repo.draft(msg.from_user.id)
        if draft["stage"] == "context":
            draft = await svc.set_context(msg.from_user.id, msg.text)
            await msg.answer(await page_description(repo, draft["page_key"], draft.get("context_slug")),
                             reply_markup=type_menu())
            return
        draft = await svc.accept_text(msg.from_user.id, msg.text)
        if draft["stage"] == "new_body":
            await msg.answer("Опишите пользу и содержимое новой страницы (20–2000 символов).")
        else:
            pages = {p["_id"]: p for p in await repo.pages()}
            await msg.answer(confirm_text(draft, pages), reply_markup=confirm_menu())
    except (SiteFeedbackError, ValueError) as exc:
        await msg.answer("⚠️ " + str(exc) + "\n/cancel — отменить.")
    except Exception:
        logger.exception("Site feedback draft failed")
        await msg.answer("Ошибка сохранения черновика. Попробуйте позже.")
