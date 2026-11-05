"""Контракт хранилища состояния сценариев меню (по одному документу на пользователя)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MenuFlowRepositoryPort(Protocol):
    async def get(self, user_id: int) -> dict | None: ...

    async def save(self, user_id: int, data: dict) -> None:
        """Полностью заменить состояние пользователя; обновляет ``updated_at``."""
        ...

    async def delete(self, user_id: int) -> None: ...
