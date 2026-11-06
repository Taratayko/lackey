"""Меню бота: Reply Keyboard `(つ✧ω✧)つ`, inline-экраны, сценарии «В гости» / «Вписаться».

Только transport-слой Telegram. Шаги и проверки — ``MenuFlowService``,
доставка заявки — ``MenuRequestRelay`` (общий DeliveryPipeline).
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputRichMessage, Message

from adapters.telegram import TelegramChannelAdapter
from adapters.telegram.menu_keyboards import (
    back_kb, city_kb, commune_kb, main_menu_kb, menu_back_kb, when_kb,
)
from application.services.menu_flow import MenuFlowError, MenuFlowService, MenuRequestRelay
from database import db
from domain.menu_flow import GUEST, STAY
from presentation.menu_texts import (
    ASK_CITY, ASK_COMMUNE, ASK_DATE, ASK_DAYS, ASK_TEXT, ASK_WHEN, MENU_BUTTON, MENU_TITLE,
    NO_COMMUNES, NOT_DELIVERED_NO_ACCESS, SEND_FAILED, STALE_BUTTON, WRITE_TEXT,
    rich_screen,
)

from .admin import build_full_list_text
from .common import is_real_reply, resolve_chat_and_text, send_rich, sender_mention

router = Router()
logger = logging.getLogger(__name__)

# Эти команды не должны сбрасывать начатый сценарий.
KEEP_FLOW_COMMANDS = frozenset({"/anon", "/deanon"})


def flow_service() -> MenuFlowService:
    return MenuFlowService(db.get_menu_flow_repository(), db.get_repositories().chats)


async def drop_site_draft(user_id: int) -> None:
    """Черновик /communesite перехватывает текст раньше DM — снимаем при входе в меню."""
    repo = db.get_site_feedback_repository()
    if await repo.draft(user_id):
        await repo.clear_draft(user_id)


async def edit_screen(msg: Message, html: str, markup: InlineKeyboardMarkup | None) -> None:
    """Заменить экран меню на месте. ``html`` — Rich HTML (редактирование Rich → Rich).

    ``parse_mode=None`` и ``link_preview_options=None`` обязательны: иначе aiogram подставит
    дефолтный parse_mode=HTML бота и отправит его вместе с ``rich_message``.
    """
    try:
        await msg.edit_text(
            rich_message=InputRichMessage(html=html), reply_markup=markup,
            parse_mode=None, link_preview_options=None,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def strip_keyboard(bot: Bot, chat_id: int, message_id: int | None) -> None:
    """Убрать кнопки с прошлого экрана; ошибки (удалено/устарело) не важны."""
    if not message_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:
        pass


async def render_stage(svc: MenuFlowService, flow: dict) -> tuple[str, InlineKeyboardMarkup]:
    stage = flow["stage"]
    if stage == "city":
        return rich_screen(ASK_CITY), city_kb()
    if stage == "commune":
        communes = await svc.communes_for_city(flow["city"])
        if not communes:
            return rich_screen(NO_COMMUNES), back_kb()
        return rich_screen(ASK_COMMUNE), commune_kb(communes)
    if stage == "when":
        return rich_screen(ASK_WHEN), when_kb(flow["kind"])
    if stage == "date":
        return rich_screen(ASK_DATE), back_kb()
    if stage == "days":
        return rich_screen(ASK_DAYS), back_kb()
    return rich_screen(ASK_TEXT), back_kb()


# ═══════════════════════════════════════════════════════════════════════════
#  Точка входа: Reply Keyboard → главное меню
# ═══════════════════════════════════════════════════════════════════════════

@router.message(F.chat.type == "private", F.text == MENU_BUTTON)
async def open_menu(msg: Message) -> None:
    if msg.from_user:
        await flow_service().clear(msg.from_user.id)
        await drop_site_draft(msg.from_user.id)
    await send_rich(msg, rich_screen(MENU_TITLE), reply_markup=main_menu_kb())


@router.message(F.chat.type == "private", F.text.startswith("/"))
async def reset_flow_on_command(msg: Message) -> None:
    """Любая другая команда отменяет сценарий; сама команда обрабатывается дальше."""
    command = (msg.text or "").split(maxsplit=1)[0].split("@")[0].lower()
    if msg.from_user and command not in KEEP_FLOW_COMMANDS:
        await flow_service().clear(msg.from_user.id)
    raise SkipHandler


# ═══════════════════════════════════════════════════════════════════════════
#  Inline-кнопки
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("lk:"))
async def on_callback(call: CallbackQuery) -> None:
    msg = call.message
    if not isinstance(msg, Message) or msg.chat.type != "private":
        await call.answer()
        return
    user_id = call.from_user.id
    parts = (call.data or "").split(":")
    action, arg = parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else None
    svc = flow_service()
    try:
        await dispatch_callback(svc, msg, user_id, action, arg)
    except MenuFlowError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    except Exception:
        # ValueError (битый callback), BadRequest Telegram, недоступность Mongo.
        logger.exception("Menu callback failed: %s", call.data)
        await call.answer(STALE_BUTTON, show_alert=True)
        return
    await call.answer()


async def dispatch_callback(
    svc: MenuFlowService, msg: Message, user_id: int, action: str, arg: str | None,
) -> None:
    # Экраны без сценария.
    if action == "menu":
        await svc.clear(user_id)
        await edit_screen(msg, rich_screen(MENU_TITLE), main_menu_kb())
        return
    if action == "write":
        await svc.clear(user_id)
        await edit_screen(msg, WRITE_TEXT, menu_back_kb())
        return
    if action == "list":
        await svc.clear(user_id)
        # Тот же Rich-список, что у /list и /start в ЛС.
        await edit_screen(msg, await build_full_list_text(), menu_back_kb())
        return
    if action == "go" and arg in (GUEST, STAY):
        await drop_site_draft(user_id)
        flow = await svc.begin(user_id, arg, msg.message_id)
        await edit_screen(msg, *await render_stage(svc, flow))
        return

    # Шаги сценария: кнопка должна быть с актуального экрана.
    flow = await svc.get(user_id)
    if not flow or flow.get("screen_message_id") != msg.message_id:
        raise MenuFlowError(STALE_BUTTON)
    if action == "city" and arg:
        flow = await svc.choose_city(user_id, arg)
    elif action == "c" and arg:
        flow = await svc.choose_commune(user_id, int(arg))
    elif action == "when" and arg:
        flow = await svc.choose_when(user_id, arg)
    elif action == "back":
        flow = await svc.back(user_id)
        if flow is None:
            await edit_screen(msg, rich_screen(MENU_TITLE), main_menu_kb())
            return
    else:
        raise MenuFlowError(STALE_BUTTON)
    await edit_screen(msg, *await render_stage(svc, flow))


# ═══════════════════════════════════════════════════════════════════════════
#  Текстовые шаги: «на сколько дней» и «прилагаемое послание»
# ═══════════════════════════════════════════════════════════════════════════

class InTextStage(Filter):
    """Личный текст без reply, пока у пользователя открыт сценарий на текстовом шаге."""

    async def __call__(self, msg: Message) -> bool:
        if msg.chat.type != "private" or not msg.text or msg.text.startswith("/"):
            return False
        if not msg.from_user or is_real_reply(msg):
            return False
        flow = await db.get_menu_flow_repository().get(msg.from_user.id)
        if not flow or flow.get("stage") not in {"date", "days", "text"}:
            return False
        # Черновик /communesite, начатый позже сценария, имеет приоритет.
        return not await db.get_site_feedback_repository().draft(msg.from_user.id)


@router.message(F.chat.type == "private", F.text, InTextStage())
async def on_flow_text(msg: Message, bot: Bot) -> None:
    user_id = msg.from_user.id
    svc = flow_service()

    # «Башня! привет» посреди сценария — это обычное сообщение коммуне, а не послание.
    if await resolve_chat_and_text(msg.text):
        await svc.clear(user_id)
        raise SkipHandler

    flow = await svc.get(user_id)
    if not flow:
        return
    try:
        if flow["stage"] in {"date", "days"}:
            accept = svc.accept_date if flow["stage"] == "date" else svc.accept_days
            flow = await accept(user_id, msg.text)
            await strip_keyboard(bot, msg.chat.id, flow.get("screen_message_id"))
            html, markup = await render_stage(svc, flow)  # «date» → «days», «days» → «text»
            prompt = await send_rich(msg, html, reply_markup=markup)
            await svc.set_screen(user_id, flow, prompt.message_id)
            return
        flow, chat, body = await svc.finish_text(user_id, msg.text)
    except MenuFlowError as exc:
        await msg.reply(str(exc))
        return
    try:
        await deliver_request(bot, msg, svc, flow, chat, body)
    except Exception:
        logger.exception("Menu request delivery failed")
        await msg.reply(SEND_FAILED)


async def send_main_menu(msg: Message) -> None:
    """После отправленного послания снова показать главное меню (новым сообщением)."""
    try:
        await send_rich(msg, rich_screen(MENU_TITLE), reply_markup=main_menu_kb())
    except Exception:
        # Заявка уже доставлена: сбой показа меню не должен выглядеть как «не доставлено».
        logger.exception("Cannot show main menu after delivered request")


async def deliver_request(
    bot: Bot, msg: Message, svc: MenuFlowService, flow: dict, chat: dict, body: str,
) -> None:
    user_id = msg.from_user.id
    relay = MenuRequestRelay(db.get_repositories(), TelegramChannelAdapter(bot))
    result = await relay.send_request(
        flow, chat, body,
        origin_chat_id=msg.chat.id, origin_message_id=msg.message_id,
        user_id=user_id, visible_name=sender_mention(msg),
    )
    if result.delivered:
        await svc.clear(user_id)
        await strip_keyboard(bot, msg.chat.id, flow.get("screen_message_id"))
        if result.user_error:  # частичная доставка / не записан mapping
            await msg.reply(result.user_error)
        await send_main_menu(msg)
    elif result.user_error:
        await msg.reply(result.user_error)  # сценарий остаётся: можно отправить ещё раз
    elif result.skipped:
        await msg.reply(NOT_DELIVERED_NO_ACCESS)
