"""Словарь и чистые правила сценариев меню «В гости» / «Вписаться».

Здесь нет ни aiogram, ни Mongo: только константы и функции, которые легко
тестировать. Порядок экранов взят из ТЗ (lakey-tz.md, разделы 5 и 6).
"""
from __future__ import annotations

GUEST = "guest"   # «В гости»
STAY = "stay"     # «Вписаться»

# Порядок экранов; «text» всегда последний.
STAGES: dict[str, tuple[str, ...]] = {
    GUEST: ("city", "commune", "when", "text"),
    STAY: ("city", "commune", "when", "date", "days", "text"),
}

# Шаг «date» (ввод своей даты) есть только у «Вписаться» и только если на «Когда?»
# выбрано «В другой день»; во всех остальных случаях он пропускается.
OTHER_DAY = "other"
CONDITIONAL_STAGES = frozenset({"date"})

# (callback, подпись кнопки, код группы коммун в БД). Порядок — как в ТЗ.
CITIES: tuple[tuple[str, str, str], ...] = (
    ("msk", "Москва", "msk"),
    ("spb", "Питер", "spb"),
    ("vlk", "Владик", "vlk"),
)

# (callback, подпись кнопки, слово в итоговом сообщении)
WHEN: tuple[tuple[str, str, str], ...] = (
    ("today", "Сегодня", "сегодня"),
    ("tomorrow", "Завтра", "завтра"),
    ("after", "Послезавтра", "послезавтра"),
)

BTN_OTHER_DAY = "В другой день"  # подпись кнопки (только «Вписаться»)

MAX_DAYS = 60       # верхняя граница «на сколько дней»
MAX_DATE_LEN = 40    # длина своей даты («15 ноября», «в пятницу вечером»)
MAX_TEXT_LEN = 2000  # длина «прилагаемого послания»; с шапкой влезает в 4096


def city_group(code: str) -> str | None:
    """Код группы чатов в БД для кода города из кнопки."""
    return next((group for key, _, group in CITIES if key == code), None)


def when_word(key: str) -> str | None:
    """«сегодня» / «завтра» / «послезавтра» по коду кнопки."""
    return next((word for k, _, word in WHEN if k == key), None)


def when_phrase(flow: dict) -> str | None:
    """Что подставить в заявку вместо «когда»: слово с кнопки или введённая дата."""
    if flow.get("when") == OTHER_DAY:
        return (flow.get("date") or "").strip() or None
    return when_word(flow.get("when", ""))


def parse_date(raw: str) -> str:
    """Своя дата одной строкой: пробелы схлопнуты, 1..MAX_DATE_LEN символов, иначе ValueError."""
    value = " ".join((raw or "").split())
    if not value or len(value) > MAX_DATE_LEN:
        raise ValueError("bad date")
    return value


def _active_stages(kind: str, when: str | None) -> tuple[str, ...]:
    return tuple(
        s for s in STAGES[kind]
        if s not in CONDITIONAL_STAGES or (s == "date" and when == OTHER_DAY)
    )


def plural_days(n: int) -> str:
    """Склонение: 1 день, 2–4 дня, 5–20 дней, 21 день, 22 дня…"""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return "дней"
    last = n % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def parse_days(raw: str) -> int:
    """Целое число дней 1..MAX_DAYS или ValueError."""
    value = (raw or "").strip()
    if not value.isdecimal() or len(value) > 3:
        raise ValueError("not a number")
    days = int(value)
    if not 1 <= days <= MAX_DAYS:
        raise ValueError("out of range")
    return days


def previous_stage(kind: str, stage: str, when: str | None = None) -> str | None:
    """Предыдущий экран или None, если мы на первом («Назад» → главное меню)."""
    order = _active_stages(kind, when)
    index = order.index(stage)
    return order[index - 1] if index else None


def next_stage(kind: str, stage: str, when: str | None = None) -> str:
    order = _active_stages(kind, when)
    return order[order.index(stage) + 1]
