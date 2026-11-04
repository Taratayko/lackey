"""VK Bots Long Poll runner skeleton.

Runner пока не подключён к `bot.py`. Он нужен, чтобы следующий патч мог
запустить VK runtime без переписывания клиента и маппера.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from domain.models import IncomingMessage

from .client import VkApiClient, VkLongPollServer
from .mapper import vk_message_update_to_domain

VkMessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class VkLongPollRunner:
    """Небольшой bounded-friendly runner для VK Bots Long Poll."""

    def __init__(
        self,
        client: VkApiClient,
        group_id: str | int,
        wait: int = 25,
        retry_delay: float = 3.0,
    ) -> None:
        self._client = client
        self._group_id = group_id
        self._wait = wait
        self._retry_delay = retry_delay
        self._server: VkLongPollServer | None = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run_forever(self, handler: VkMessageHandler) -> None:
        """Читать Long Poll updates и отдавать message_new в handler."""
        self._server = await self._client.groups_get_long_poll_server(self._group_id)
        while not self._stop_event.is_set():
            try:
                await self._run_once(handler)
            except Exception:
                self._server = None
                await asyncio.sleep(self._retry_delay)
                if not self._stop_event.is_set():
                    self._server = await self._client.groups_get_long_poll_server(self._group_id)

    async def _run_once(self, handler: VkMessageHandler) -> None:
        if self._server is None:
            self._server = await self._client.groups_get_long_poll_server(self._group_id)

        response = await self._client.long_poll_check(self._server, wait=self._wait)
        if "failed" in response:
            self._server = await self._client.groups_get_long_poll_server(self._group_id)
            return

        self._server = VkLongPollServer(
            server=self._server.server,
            key=self._server.key,
            ts=str(response.get("ts", self._server.ts)),
        )

        for update in response.get("updates", []):
            await self._handle_update(update, handler)

    async def _handle_update(self, update: dict[str, Any], handler: VkMessageHandler) -> None:
        message = vk_message_update_to_domain(update)
        if message is not None:
            await handler(message)
