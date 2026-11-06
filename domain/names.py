"""Сопоставление названий коммун без различия «е» и «ё».

Чистые функции без Telegram/Mongo: используются и поиском коммуны
(database.get_chat_by_name), и админ-командами /addalias и /removealias,
чтобы «Всё просто» и «Все просто» считались одним и тем же именем.
"""
from __future__ import annotations

import re

_YO_TABLE = str.maketrans({"ё": "е", "Ё": "Е"})


def fold_name(value: str) -> str:
    """Ключ сравнения: без пробелов по краям, нижний регистр, «ё» → «е»."""
    return value.strip().lower().translate(_YO_TABLE)


def same_name(left: str, right: str) -> bool:
    """True, если имена совпадают с точностью до регистра и е/ё."""
    return fold_name(left) == fold_name(right)


def yo_insensitive_regex(value: str) -> str:
    """Regex (для Mongo $regex), совпадающий с value при любом выборе е/ё.

    Остальные символы экранируются, поэтому имя вроде «a.b (c)» остаётся
    обычным текстом, а не шаблоном.
    """
    parts: list[str] = []
    for ch in fold_name(value):
        parts.append("[её]" if ch == "е" else re.escape(ch))
    return "^" + "".join(parts) + "$"
