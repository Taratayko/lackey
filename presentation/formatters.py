"""
Общие форматтеры вывода.

Тонкий слой для безопасного HTML в Telegram и сборки шапок ретрансляции из
шаблонов ``presentation.texts`` (RELAY_*). Бизнес-логики здесь нет.
"""
from html import escape as _html_escape
from typing import Any

from presentation.texts import (
    RELAY_ARROW,
    RELAY_HEADER_CHAT,
    RELAY_HEADER_DM,
    RELAY_HEADER_REPLY,
)


def escape_html(value: Any) -> str:
    """Экранирует пользовательский/БД-текст для parse_mode=HTML."""
    if value is None:
        return ""
    return _html_escape(str(value), quote=False)


def escape_html_attr(value: Any) -> str:
    """Экранирует значение HTML-атрибута, например href."""
    if value is None:
        return ""
    return _html_escape(str(value), quote=True)


def html_link(label: Any, url: Any) -> str:
    """Возвращает безопасную HTML-ссылку для Telegram parse_mode=HTML."""
    return f'<a href="{escape_html_attr(url)}">{escape_html(label)}</a>'


# ── Шапки ретрансляции (обычный parse_mode=HTML) ────────────────────────────

def chat_relay_header(chat_name: Any, sender_label: Any) -> str:
    """«from Зиккурат от @user» — шапка сообщения из беседы."""
    return RELAY_HEADER_CHAT.format(chat_name=escape_html(chat_name), sender=escape_html(sender_label))


def dm_relay_header(sender_label: Any) -> str:
    """«from ЛС от @user» (или «от анонима») — шапка сообщения из ЛС."""
    return RELAY_HEADER_DM.format(sender=escape_html(sender_label))


def reply_relay_header(sender_label: Any) -> str:
    """«от @user» — шапка ответа в reply-цепочке."""
    return RELAY_HEADER_REPLY.format(sender=escape_html(sender_label))


def format_chat_relay_text(chat_name: Any, sender_label: Any, text: Any) -> str:
    """Текст из беседы: шапка + «↳ » + текст."""
    return f"{chat_relay_header(chat_name, sender_label)}\n{RELAY_ARROW}{escape_html(text)}"


def format_dm_relay_text(sender_label: Any, text: Any) -> str:
    """Текст из ЛС: шапка + «↳ » + пробел + текст.

    Пробел после стрелки сохранён как в старом handlers.py (там он есть только
    у ЛС-текста; у остальных шапок его нет).
    """
    return f"{dm_relay_header(sender_label)}\n{RELAY_ARROW} {escape_html(text)}"


def format_dm_media_header(sender_label: Any, extra_text: Any = "") -> str:
    """Шапка/подпись медиа из ЛС; подпись пользователя добавляется после «↳ »."""
    header = dm_relay_header(sender_label)
    if extra_text:
        return f"{header}\n{RELAY_ARROW}{escape_html(extra_text)}"
    return header


def format_reply_relay_text(sender_label: Any, text: Any = None) -> str:
    """Ответ в reply-цепочке: шапка, а если есть текст — ещё «↳ » + текст."""
    header = reply_relay_header(sender_label)
    if text:
        return f"{header}\n{RELAY_ARROW}{escape_html(text)}"
    return header
