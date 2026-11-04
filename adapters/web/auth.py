"""Минимальная token-auth заготовка для будущего Web/Site adapter."""
from __future__ import annotations

from collections.abc import Mapping


class WebAuthError(PermissionError):
    """Ошибка авторизации web/API запроса."""


def _get_header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def extract_bearer_token(headers: Mapping[str, str] | None) -> str | None:
    """Достать Bearer token из HTTP headers."""
    value = _get_header(headers, "authorization")
    if not value:
        return None
    prefix = "bearer "
    if value.lower().startswith(prefix):
        return value[len(prefix) :].strip()
    return None


def validate_web_api_token(
    headers: Mapping[str, str] | None,
    expected_token: str | None,
) -> None:
    """Проверить token будущего site/API слоя.

    Если `expected_token` пустой, проверка отключена. Это удобно для skeleton и
    локальных smoke-тестов, но при включении Web runtime токен должен быть задан
    в `.env`.
    """
    if not expected_token:
        return

    token = extract_bearer_token(headers) or _get_header(headers, "x-lackey-token")
    if token != expected_token:
        raise WebAuthError("invalid web api token")
