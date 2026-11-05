"""Клавиатуры меню: постоянная Reply Keyboard и inline-экраны (ТЗ, разделы 1–6).

callback_data: ``lk:<action>[:<arg>]`` (< 64 байт; chat_id — отрицательное число).
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)

from domain.menu_flow import BTN_OTHER_DAY, CITIES, GUEST, OTHER_DAY, STAY, WHEN
from presentation.menu_texts import (
    BTN_BACK, BTN_GUEST, BTN_LIST, BTN_STAY, BTN_SUPPORT, BTN_WRITE,
    KEYBOARD_PLACEHOLDER, MENU_BUTTON, SUPPORT_URL,
)

PREFIX = "lk"


def cb(action: str, arg: str | int | None = None) -> str:
    return f"{PREFIX}:{action}" if arg is None else f"{PREFIX}:{action}:{arg}"


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def reply_menu_keyboard() -> ReplyKeyboardMarkup:
    """Единственная постоянная кнопка `(つ✧ω✧)つ` — не пропадает из клавиатуры."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MENU_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=KEYBOARD_PLACEHOLDER,
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(BTN_STAY, cb("go", "stay")), _button(BTN_GUEST, cb("go", "guest"))],
        [_button(BTN_WRITE, cb("write")), _button(BTN_LIST, cb("list"))],
        [InlineKeyboardButton(text=BTN_SUPPORT, url=SUPPORT_URL)],
    ])


def back_kb(action: str = "back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button(BTN_BACK, cb(action))]])


def menu_back_kb() -> InlineKeyboardMarkup:
    """«Назад» с информационных экранов — в главное меню."""
    return back_kb("menu")


def city_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(label, cb("city", key)) for key, label, _ in CITIES],
        [_button(BTN_BACK, cb("back"))],
    ])


def commune_kb(communes: list[dict]) -> InlineKeyboardMarkup:
    """Одним столбцом, в конце «Назад»."""
    rows = [[_button(c["name"], cb("c", c["chat_id"]))] for c in communes]
    rows.append([_button(BTN_BACK, cb("back"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def when_kb(kind: str = GUEST) -> InlineKeyboardMarkup:
    days_row = [_button(label, cb("when", key)) for key, label, _ in WHEN]
    if kind == STAY:
        # «Вписаться»: ещё и «В другой день» (своя дата текстом).
        return InlineKeyboardMarkup(inline_keyboard=[
            days_row,
            [_button(BTN_OTHER_DAY, cb("when", OTHER_DAY)), _button(BTN_BACK, cb("back"))],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[days_row + [_button(BTN_BACK, cb("back"))]])
