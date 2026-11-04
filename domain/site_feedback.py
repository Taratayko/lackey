"""Communars site-feedback vocabulary and stable route registry."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote
import re


@dataclass(frozen=True)
class SitePage:
    key: str
    title: str
    route: str
    access: str = "public"
    order: int = 0


# Based on the current PHP router; {slug} denotes a dynamic route, not a real record.
PAGES = (
    SitePage("home", "Главная / каталог коммун", "/", order=10),
    SitePage("commune_profile", "Страница коммуны", "/communes/{slug}/", order=20),
    SitePage("commune_members", "Участники коммуны", "/communes/{slug}/members/", order=30),
    SitePage("commune_join", "Вступление в коммуну", "/communes/{slug}/join/", order=40),
    SitePage("events", "Каталог мероприятий", "/events/", order=50),
    SitePage("event_profile", "Страница мероприятия", "/events/{slug}/", order=60),
    SitePage("event_create", "Создание мероприятия", "/events/create/", order=70),
    SitePage("login", "Вход на сайт", "/login/", order=80),
    SitePage("admin", "Административная панель", "/admin/", "admin_only", 90),
    SitePage("moderation", "Модерация", "/admin/moderation/", "admin_only", 100),
)
PAGE_MAP = {page.key: page for page in PAGES}
KINDS = {"page_feedback", "new_page"}
FEEDBACK_TYPES = {"improvement": "Улучшение", "bug": "Ошибка", "question": "Замечание / вопрос"}
STATUS_NAMES = {
    "new": "Новое", "in_review": "На рассмотрении", "accepted": "Принято",
    "rejected": "Отклонено", "implemented": "Реализовано",
}
STATUS_NEXT = {
    "new": {"in_review", "accepted", "rejected"},
    "in_review": {"accepted", "rejected"},
    "accepted": {"implemented", "in_review"},
    "rejected": {"in_review"},
    "implemented": {"in_review"},
}
PAGE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def validate_slug(raw: str) -> str:
    value = raw.strip().lower()
    if not SLUG_PATTERN.fullmatch(value):
        raise ValueError("Slug: только латиница a-z, цифры и дефис; длина 1–64.")
    return value


def site_url(base_url: str, page: SitePage, slug: str | None = None) -> str | None:
    """Never manufacture concrete URLs for unspecified or unverified slugs."""
    if "{slug}" in page.route and not slug:
        return None
    route = page.route
    if not route.startswith("/") or route.startswith("//") or ".." in route or "\\" in route:
        raise ValueError("Неверный относительный маршрут страницы.")
    if "{" in route.replace("{slug}", "") or "}" in route.replace("{slug}", ""):
        raise ValueError("Неподдерживаемый шаблон страницы.")
    if route.count("{slug}") > 1:
        raise ValueError("Неподдерживаемый шаблон страницы.")
    if slug:
        route = route.replace("{slug}", quote(validate_slug(slug), safe=""))
    return base_url.rstrip("/") + route
