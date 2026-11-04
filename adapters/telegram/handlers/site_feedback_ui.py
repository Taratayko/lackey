"""Telegram presentation for Communars feedback; no database writes."""
from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from domain.site_feedback import FEEDBACK_TYPES, PAGE_MAP, STATUS_NAMES, STATUS_NEXT


def button(text: str, callback: str):
    return InlineKeyboardButton(text=text, callback_data=callback)


def keyboard(rows: list[list[tuple[str, str]]]):
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(label, callback) for label, callback in row] for row in rows
    ])


def main_menu():
    return keyboard([
        [("📝 Оставить отзыв", "sf:review")],
        [("➕ Предложить страницу", "sf:new")],
        [("📋 Мои предложения", "sf:mine:0")],
    ])


def page_menu(pages: list[dict]):
    rows = [[(doc["title"], "sf:page:" + doc["_id"])] for doc in pages]
    rows.append([("↩️ Назад", "sf:menu")])
    return keyboard(rows)


def context_menu():
    return keyboard([
        [("Без конкретной страницы", "sf:skip")],
        [("❌ Отмена", "sf:cancel")],
    ])


def type_menu():
    rows = [[(label, "sf:type:" + key)] for key, label in FEEDBACK_TYPES.items()]
    rows.append([("❌ Отмена", "sf:cancel")])
    return keyboard(rows)


def confirm_menu():
    return keyboard([
        [("✅ Отправить", "sf:submit")],
        [("❌ Отмена", "sf:cancel")],
    ])


def my_list_menu(items: list[dict], offset: int, total: int):
    rows = [[(f"#{str(item['_id'])[-8:]} · {STATUS_NAMES[item['status']]}",
              "sf:item:" + str(item["_id"]))] for item in items]
    navigation = []
    if offset:
        navigation.append(("◀️", "sf:mine:" + str(max(0, offset - 5))))
    if offset + len(items) < total:
        navigation.append(("▶️", "sf:mine:" + str(offset + 5)))
    if navigation:
        rows.append(navigation)
    rows.append([("🏠 Меню", "sf:menu")])
    return keyboard(rows)


def author_label(item: dict) -> str:
    author = item["author"]
    username = author.get("username_snapshot", "")
    username_label = "@" + username if username else "без username"
    return f"{escape(username_label)} · ID <code>{author['telegram_user_id']}</code>"


def item_text(item: dict, *, admin: bool = False) -> str:
    title = PAGE_MAP.get(item.get("page_key"))
    page = (item.get("page_title") or (title.title if title else None)
            or item.get("title") or "Новая страница")
    lines = [
        f"<b>Обращение #{str(item['_id'])[-8:]}</b>",
        f"Статус: <b>{STATUS_NAMES.get(item['status'], item['status'])}</b>",
        f"Тип: {'Новая страница' if item['kind'] == 'new_page' else 'Отзыв'}",
        "Раздел: " + escape(page),
    ]
    if item.get("context_slug"):
        lines.append("Slug: <code>" + escape(item["context_slug"]) + "</code>")
    if item.get("feedback_type"):
        lines.append("Категория: " + FEEDBACK_TYPES.get(item["feedback_type"], "—"))
    if item.get("title"):
        lines.append("Название: <b>" + escape(item["title"]) + "</b>")
    lines.append("\n" + escape(item.get("body", "")))
    if admin:
        lines.append("\nАвтор: " + author_label(item))
        lines.append("ID обращения: <code>" + str(item["_id"]) + "</code>")
    if item.get("decision_note"):
        lines.append("\nКомментарий модератора: " + escape(item["decision_note"]))
    history = item.get("history", [])
    if history:
        lines.append("\nИстория статусов:")
        for change in history[-8:]:
            status = STATUS_NAMES.get(change.get("to_status"), "—")
            timestamp = change.get("created_at")
            when = timestamp.strftime("%d.%m %H:%M") if timestamp else ""
            lines.append(f"• {when}: {status}")
    return "\n".join(lines)


def admin_list_menu(items: list[dict], status: str, offset: int, total: int):
    rows = [[(f"#{str(item['_id'])[-8:]} · {STATUS_NAMES.get(item['status'], '?')}",
              "sfa:item:" + str(item["_id"]))] for item in items]
    navigation = []
    if offset:
        navigation.append(("◀️", f"sfa:list:{status}:{max(0, offset - 5)}"))
    if offset + len(items) < total:
        navigation.append(("▶️", f"sfa:list:{status}:{offset + 5}"))
    if navigation:
        rows.append(navigation)
    rows.append([("🏠 Очередь", "sfa:menu")])
    return keyboard(rows)


def author_list_menu(items: list[dict], user_id: int, offset: int, total: int):
    rows = [[(f"#{str(item['_id'])[-8:]} · {STATUS_NAMES.get(item['status'], '?')}",
              "sfa:item:" + str(item["_id"]))] for item in items]
    navigation = []
    if offset:
        navigation.append(("◀️", f"sfa:author:{user_id}:{max(0, offset - 5)}"))
    if offset + len(items) < total:
        navigation.append(("▶️", f"sfa:author:{user_id}:{offset + 5}"))
    if navigation:
        rows.append(navigation)
    rows.append([("🏠 Очередь", "sfa:menu")])
    return keyboard(rows)


def admin_menu():
    rows = [[("Все", "sfa:list:all:0")]]
    rows.extend([[(label, f"sfa:list:{status}:0")] for status, label in STATUS_NAMES.items()])
    return keyboard(rows)


def admin_item_menu(item: dict):
    rows = [[(STATUS_NAMES[status], f"sfa:set:{str(item['_id'])}:{status}")]
            for status in sorted(STATUS_NEXT.get(item["status"], []))]
    rows.append([("↩️ Очередь", "sfa:menu")])
    return keyboard(rows)


def confirm_text(draft: dict, pages: dict[str, dict]) -> str:
    if draft["kind"] == "new_page":
        summary = "Предложить страницу: <b>" + escape(draft["title"]) + "</b>"
    else:
        page = pages.get(draft["page_key"], {})
        summary = "Страница: <b>" + escape(page.get("title", draft["page_key"])) + "</b>"
        if draft.get("context_slug"):
            summary += "\nSlug: <code>" + escape(draft["context_slug"]) + "</code>"
        summary += "\nТип: " + FEEDBACK_TYPES[draft["feedback_type"]]
    return ("<b>Подтвердите отправку</b>\n" + summary + "\n\n" + escape(draft["body"])
            + "\n\n🔒 Отзыв виден только администрации. Администратор видит ваш "
              "Telegram ID и username, даже если включён /anon для пересылок.")
