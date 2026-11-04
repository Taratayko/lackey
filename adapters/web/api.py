"""Framework-agnostic REST boundary для будущего сайта коммун.

Здесь нет FastAPI/Django-зависимости. Позже настоящий HTTP-layer сможет вызвать
эти методы из route handlers и не смешивать web-framework с application core.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from domain.models import IncomingMessage

from .auth import validate_web_api_token
from .mapper import domain_message_to_web_dict, web_dict_to_domain

WebIncomingHandler = Callable[[IncomingMessage], Awaitable[Any]]


class WebApiAdapter:
    """Минимальный controller/service для site REST adapter."""

    def __init__(
        self,
        incoming_handler: WebIncomingHandler | None = None,
        api_token: str | None = None,
    ) -> None:
        self._incoming_handler = incoming_handler
        self._api_token = api_token

    def parse_incoming_message(self, payload: dict[str, Any]) -> IncomingMessage:
        """Преобразовать JSON payload сайта в доменное сообщение."""
        return web_dict_to_domain(payload)

    async def handle_incoming_message(
        self,
        payload: dict[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Обработать будущий `POST /api/messages` без привязки к framework."""
        validate_web_api_token(headers, self._api_token)
        message = self.parse_incoming_message(payload)

        handler_result = None
        if self._incoming_handler is not None:
            handler_result = await self._incoming_handler(message)

        return {
            "ok": True,
            "message": domain_message_to_web_dict(message),
            "result": handler_result,
        }
