"""Минимальный VK API client для будущего VK adapter MVP.

Модуль не подключается к runtime Telegram-бота автоматически. Он нужен как
изолированный транспортный слой для следующего этапа: VK Long Poll / Callback
→ domain.IncomingMessage → application services.

Зависимости намеренно не добавлены: используется стандартная библиотека, чтобы
Patch 12 не менял установку текущего Telegram-бота.
"""
from __future__ import annotations

import asyncio
import json
import random
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

VK_API_BASE_URL = "https://api.vk.com/method"


class VkApiError(RuntimeError):
    """Ошибка ответа VK API."""

    def __init__(self, method: str, error: dict[str, Any]) -> None:
        self.method = method
        self.error = error
        code = error.get("error_code", "unknown")
        message = error.get("error_msg", "VK API error")
        super().__init__(f"VK API {method} failed: [{code}] {message}")


@dataclass(frozen=True, slots=True)
class VkLongPollServer:
    """Параметры VK Bots Long Poll server."""

    server: str
    key: str
    ts: str


class VkApiClient:
    """Тонкий клиент VK API.

    Это не полноценный SDK. Здесь только минимум, который понадобится адаптеру:
    отправка текста и получение параметров Bots Long Poll.
    """

    def __init__(
        self,
        token: str,
        api_version: str = "5.199",
        base_url: str = VK_API_BASE_URL,
    ) -> None:
        self._token = token
        self._api_version = api_version
        self._base_url = base_url.rstrip("/")

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Вызвать VK API method и вернуть поле response."""
        return await asyncio.to_thread(self._call_sync, method, params or {})

    def _call_sync(self, method: str, params: dict[str, Any]) -> Any:
        url = f"{self._base_url}/{method}"
        payload = {
            **params,
            "access_token": self._token,
            "v": self._api_version,
        }
        body = urllib.parse.urlencode(payload, doseq=True).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        if "error" in data:
            raise VkApiError(method, data["error"])
        return data.get("response")

    async def messages_send(
        self,
        peer_id: str | int,
        message: str,
        random_id: int | None = None,
    ) -> int:
        """Отправить текстовое сообщение в peer_id и вернуть id сообщения VK."""
        response = await self.call(
            "messages.send",
            {
                "peer_id": int(peer_id),
                "message": message,
                "random_id": random_id if random_id is not None else random.randint(1, 2**31 - 1),
            },
        )
        return int(response)

    async def groups_get_long_poll_server(self, group_id: str | int) -> VkLongPollServer:
        """Получить server/key/ts для VK Bots Long Poll."""
        response = await self.call("groups.getLongPollServer", {"group_id": int(group_id)})
        return VkLongPollServer(
            server=str(response["server"]),
            key=str(response["key"]),
            ts=str(response["ts"]),
        )

    async def long_poll_check(
        self,
        server: VkLongPollServer,
        wait: int = 25,
    ) -> dict[str, Any]:
        """Выполнить один запрос a_check к Bots Long Poll server."""
        return await asyncio.to_thread(self._long_poll_check_sync, server, wait)

    def _long_poll_check_sync(self, server: VkLongPollServer, wait: int) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "act": "a_check",
                "key": server.key,
                "ts": server.ts,
                "wait": int(wait),
            }
        )
        url = f"{server.server}?{query}"
        with urllib.request.urlopen(url, timeout=wait + 10) as response:
            return json.loads(response.read().decode("utf-8"))
