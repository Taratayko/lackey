"""Owner-only feedback moderation, Telegram commands and inline controls."""
from __future__ import annotations

import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from application.services.site_feedback import SiteFeedbackError, SiteFeedbackService
from config import settings
from database import db
from domain.site_feedback import STATUS_NAMES
from .site_feedback_ui import (admin_item_menu, admin_list_menu, admin_menu,
                               author_list_menu, item_text)

router = Router()
logger = logging.getLogger(__name__)


def service():
    return SiteFeedbackService(db.get_site_feedback_repository(), settings.ADMIN_ID)


def authorized(user, chat):
    return bool(user and user.id == settings.ADMIN_ID and chat.type == "private")


def admin_selector(status: str):
    if status == "all":
        return {}
    if status in STATUS_NAMES:
        return {"status": status}
    raise SiteFeedbackError("Неизвестный фильтр статуса.")


async def send_queue(target, actor: int, status: str, offset: int):
    svc = service()
    docs, total = await svc.admin_items(actor, admin_selector(status), offset)
    await target.answer(
        f"📥 Обращения · {escape(STATUS_NAMES.get(status, 'Все'))}: {total}",
        reply_markup=admin_list_menu(docs, status, offset, total),
    )


async def send_author_queue(target, actor: int, user_id: int, offset: int = 0):
    selector = {"author.telegram_user_id": user_id}
    items, total = await service().admin_items(actor, selector, offset)
    await target.answer(
        f"Автор · Telegram ID <code>{user_id}</code> · обращений: {total}",
        reply_markup=author_list_menu(items, user_id, offset, total),
    )


async def send_card(target, actor: int, item_id: str):
    svc = service()
    svc.assert_owner(actor)
    item = await db.get_site_feedback_repository().get_item(item_id)
    if not item:
        raise SiteFeedbackError("Обращение не найдено.")
    await target.answer(item_text(item, admin=True), reply_markup=admin_item_menu(item))


async def notify_author(bot, item, *, note: bool = False):
    """Errors are nonfatal: Mongo status changes remain authoritative."""
    status = STATUS_NAMES.get(item["status"], item["status"])
    text = (f"🏛 Ваше обращение #{str(item['_id'])[-8:]} обновлено.\n"
            f"Статус: <b>{status}</b>")
    if item.get("decision_note"):
        text += "\nКомментарий: " + escape(item["decision_note"])
    try:
        await bot.send_message(item["author"]["telegram_user_id"], text)
    except Exception:
        logger.warning("Could not deliver site feedback notification, item=%s", item["_id"], exc_info=True)


@router.message(Command("sitefeedback"))
async def admin_command(msg: Message):
    if not authorized(msg.from_user, msg.chat):
        await msg.reply("Команда доступна только администратору в ЛС.")
        return
    args = (msg.text or "").partition(" ")[2].strip()
    svc = service()
    repo = db.get_site_feedback_repository()
    try:
        if not args:
            await msg.answer("📥 Обратная связь по сайту\n"
                             "Поиск: /sitefeedback @username или /sitefeedback 123456789\n"
                             "Статусы: /sitefeedback new", reply_markup=admin_menu())
            return
        if args in STATUS_NAMES or args == "all":
            await send_queue(msg, msg.from_user.id, args, 0)
            return
        if re.fullmatch(r"[0-9a-fA-F]{24}", args):
            await send_card(msg, msg.from_user.id, args)
            return
        ids = []
        if args.startswith("@"):
            ids = await repo.user_ids_for_username(args)
            if not ids:
                await msg.reply("По такому username обращений не найдено.\n"
                                "Поиск охватывает только имена, которые бот видел с момента обновления.")
                return
        elif args.isdecimal() and len(args) <= 20:
            ids = [int(args)]
        else:
            raise SiteFeedbackError("Используйте статус, Telegram ID или @username.")
        if len(ids) > 1:
            await msg.answer(
                "⚠️ Username встречался у нескольких Telegram ID. "
                "Выберите конкретный ID:\n" +
                "\n".join(f"/sitefeedback {uid}" for uid in ids)
            )
            return
        await send_author_queue(msg, msg.from_user.id, ids[0])
    except SiteFeedbackError as exc:
        await msg.answer("⚠️ " + escape(str(exc)))
    except Exception:
        logger.exception("sitefeedback admin command failed")
        await msg.answer("Ошибка базы данных; повторите позже.")


@router.message(Command("sfnote"))
async def admin_note(msg: Message, bot):
    if not authorized(msg.from_user, msg.chat):
        await msg.reply("Недостаточно прав.")
        return
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await msg.reply("Формат: /sfnote &lt;полный ID обращения&gt; текст комментария")
        return
    try:
        item = await service().add_note(msg.from_user.id, parts[1], parts[2])
        await msg.reply("✅ Комментарий сохранён.")
        await notify_author(bot, item, note=True)
    except SiteFeedbackError as exc:
        await msg.reply("⚠️ " + escape(str(exc)))
    except Exception:
        logger.exception("sitefeedback note failed")
        await msg.reply("Ошибка при сохранении комментария.")


@router.callback_query(F.data.startswith("sfa:"))
async def admin_callback(call: CallbackQuery, bot):
    if not call.message or not authorized(call.from_user, call.message.chat):
        await call.answer("Недостаточно прав.", show_alert=True)
        return
    await call.answer()
    tokens = (call.data or "").split(":")
    action = tokens[1] if len(tokens) > 1 else ""
    try:
        if action == "menu":
            await call.message.answer("📥 Управление отзывами", reply_markup=admin_menu())
        elif action == "list" and len(tokens) == 4:
            raw = tokens[3]
            if not raw.isdecimal() or len(raw) > 6:
                raise SiteFeedbackError("Неверный номер страницы.")
            await send_queue(call.message, call.from_user.id, tokens[2], min(100000, int(raw)))
        elif action == "author" and len(tokens) == 4:
            if not tokens[2].isdecimal() or len(tokens[2]) > 20 or not tokens[3].isdecimal():
                raise SiteFeedbackError("Неверный Telegram ID или номер страницы.")
            if len(tokens[3]) > 6:
                raise SiteFeedbackError("Неверный номер страницы.")
            await send_author_queue(call.message, call.from_user.id,
                                    int(tokens[2]), min(int(tokens[3]), 100000))
        elif action == "item" and len(tokens) == 3:
            await send_card(call.message, call.from_user.id, tokens[2])
        elif action == "set" and len(tokens) == 4:
            updated = await service().change_status(call.from_user.id, tokens[2], tokens[3])
            await call.message.answer("✅ Статус изменён.")
            await notify_author(bot, updated)
            await send_card(call.message, call.from_user.id, tokens[2])
        else:
            await call.message.answer("Кнопка устарела. /sitefeedback")
    except SiteFeedbackError as exc:
        await call.message.answer("⚠️ " + escape(str(exc)))
    except Exception:
        logger.exception("sitefeedback admin callback failed")
        await call.message.answer("Ошибка обработки, попробуйте позже.")
