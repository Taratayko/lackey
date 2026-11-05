"""Тексты меню (inline-экраны и итоговые заявки «В гости» / «Вписаться»).

Экраны меню — Rich Message (как /start, /help и /list): ``sendRichMessage`` при
открытии и ``editMessageText(rich_message=...)`` при переходах, поэтому все они
правятся на месте. Rich-разметка (<p>, <br/>, <blockquote>) берётся из констант
``presentation.texts`` — здесь ничего не копируется.

Итоговая заявка в коммуну — обычный parse_mode=HTML, как остальные шапки релеев.
"""
from __future__ import annotations

from domain.menu_flow import plural_days
from presentation.formatters import escape_html
from presentation.texts import DM_HELP, RELAY_ARROW

# ── Reply Keyboard и главное меню ───────────────────────────────────────────
MENU_BUTTON = "(つ✧ω✧)つ"
MENU_TITLE = "🪁Меню:"
KEYBOARD_PLACEHOLDER = "НазваниеКоммуны! текст"
SUPPORT_URL = "https://t.me/thetrtk"

BTN_STAY = "Вписаться"
BTN_GUEST = "В гости"
BTN_WRITE = "Написать"
BTN_LIST = "Список коммун"
BTN_SUPPORT = "Поддержка"
BTN_BACK = "Назад"

# ── Экран «Написать» (ТЗ, п. 3) ─────────────────────────────────────────────
# Тот же Rich-текст, что у /help в ЛС: HOW_TO_WRITE + REPLY_HINT + DM_COMMANDS_BLOCK + FOOTER.
WRITE_TEXT = DM_HELP

# ── Шаги сценариев ──────────────────────────────────────────────────────────
ASK_CITY = "Город?"
ASK_COMMUNE = "Коммуна?"
ASK_WHEN = "Когда?"
ASK_DATE = "Напиши, в какой день (напр. 15 ноября)"
ASK_DAYS = "Напиши на сколько дней (напр. 4)"
ASK_TEXT = "Напиши прилагаемое послание"

BAD_DATE = "Напиши дату коротко, до {limit} символов, например: 15 ноября"
BAD_DAYS = "Нужно число дней от 1 до {max_days}, например: 4"
TEXT_TOO_LONG = "Слишком длинно: максимум {limit} символов. Сократи, пожалуйста."
NO_COMMUNES = "Пока нет коммун, доступных для заявок в этом городе."
STALE_BUTTON = "Меню устарело — нажми (つ✧ω✧)つ"
NOT_DELIVERED_NO_ACCESS = (
    "Не доставлено 💔\nОшибка: в эту коммуну нельзя написать через меню"
)
SEND_FAILED = "Не доставлено 💔 Что-то пошло не так, попробуй ещё раз чуть позже."


def rich_screen(text: str) -> str:
    """Короткий текстовый экран («Город?», «🪁Меню:») → Rich HTML."""
    return f"<p>{escape_html(text)}</p>"


def request_header(who: str, body: str) -> str:
    return f"<b>{escape_html(who)} {body}</b>"


def format_guest_request(who: str, when: str, text: str) -> str:
    """«@user хочет прийти сегодня в гости» + «↳ текст»."""
    header = request_header(who, f"хочет прийти {when} в гости")
    return f"{header}\n{RELAY_ARROW}{escape_html(text)}"


def format_stay_request(who: str, when: str, days: int, text: str) -> str:
    """«@user хочет вписаться сегодня на 4 дня» + «↳ текст»."""
    # when может быть введён пользователем («другой день») — экранируем.
    header = request_header(who, f"хочет вписаться {escape_html(when)} на {days} {plural_days(days)}")
    return f"{header}\n{RELAY_ARROW}{escape_html(text)}"
