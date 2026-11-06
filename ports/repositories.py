"""
Contracts for persistence repositories.

These protocols describe what the application layer expects from storage.
MongoDB-specific code must live in adapters/mongo/, not in services.
Current handlers can still use database.py directly until later patches.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from domain.models import ChannelRef, MessageRef


@runtime_checkable
class ChatRepositoryPort(Protocol):
    """Repository for registered chats."""

    async def get_chat_by_id(self, chat_id: int) -> dict | None:
        """Find chat by external Telegram id."""
        ...

    async def get_chat_by_name(self, name: str) -> dict | None:
        """Find chat by slug or alias."""
        ...

    async def get_all_chats(self) -> list[dict]:
        """Return all registered chats."""
        ...

    async def get_chats_by_group(self, group: str) -> list[dict]:
        """Return chats by city/group code."""
        ...

    async def add_chat(self, data: dict) -> bool:
        """Create a chat document."""
        ...

    async def update_chat(self, chat_id: int, update: dict) -> bool:
        """Update chat fields."""
        ...

    async def remove_chat(self, chat_id: int) -> bool:
        """Remove chat from registry."""
        ...


@runtime_checkable
class ThreadRepositoryPort(Protocol):
    """Repository for relay/reply mapping records."""

    async def save_thread(
        self,
        origin_chat_id: int,
        origin_message_id: int,
        relay_chat_id: int,
        relay_message_id: int,
        *,
        origin_message_ids: list[int] | None = None,
        relay_message_ids: list[int] | None = None,
        origin_anchor_message_id: int | None = None,
        origin_topic_id: int | None = None,
        relay_topic_id: int | None = None,
        origin_channel: str = "telegram",
        relay_channel: str = "telegram",
        open_identity: bool = False,
    ) -> None:
        """Store mapping from original message to relayed message."""
        ...

    async def find_thread_by_relay(
        self,
        relay_chat_id: int,
        relay_message_id: int,
        *, channel: str = "telegram",
    ) -> dict | None:
        """Find mapping by relayed message."""
        ...

    async def find_thread_by_origin(
        self,
        origin_chat_id: int,
        origin_message_id: int,
        *, channel: str = "telegram",
    ) -> dict | None:
        """Find mapping by original message."""
        ...


@runtime_checkable
class StatsRepositoryPort(Protocol):
    """Repository for relay statistics."""

    async def increment_stat(self, from_chat_id: int, to_chat_id: int) -> None:
        """Increment relay counter between two chats."""
        ...

    async def get_stats_all(self) -> list[dict]:
        """Return all stats documents."""
        ...

    async def get_stats_recent(self, days: int) -> list[dict]:
        """Return stats with activity within the last N days."""
        ...


@runtime_checkable
class DmRepositoryPort(Protocol):
    """Repository for private-message user/session state."""

    async def is_dm_user_new(self, user_id: int) -> bool:
        """Return True when user is seen for the first time."""
        ...

    async def get_dm_session(self, user_id: int) -> dict | None:
        """Return DM session document."""
        ...

    async def set_dm_session(self, user_id: int, anonymous: bool) -> None:
        """Update DM anonymous/deanon state."""
        ...


@runtime_checkable
class DeliveryClaimPort(Protocol):
    """Atomic, durable guard against repeated delivery events."""

    async def claim(self, key: str, origin: MessageRef, target: ChannelRef) -> bool: ...

    async def complete(self, key: str, *, delivered: bool, partial: bool,
                       relay_ids: tuple[str, ...]) -> None: ...

    async def release_for_migration(self, key: str) -> None: ...


@runtime_checkable
class ModerationRepositoryPort(Protocol):
    """Журнал активных банов и мутов (источник истины — сам Telegram)."""

    async def record(
        self,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        username: str | None,
        full_name: str | None,
        actor_user_id: int | None,
        reason: str | None,
        until: datetime | None,
    ) -> None:
        """Записать/обновить действие (kind: "ban" | "mute")."""
        ...

    async def remove(self, chat_id: int, user_id: int, kind: str) -> None:
        """Удалить запись о действии."""
        ...

    async def find_by_username(self, chat_id: int, username: str, kind: str) -> dict | None:
        """Найти активную запись по username (без @, регистр не важен)."""
        ...

    async def list_active(self, chat_id: int, kind: str) -> list[dict]:
        """Активные записи чата; истёкшие муты не возвращаются."""
        ...


@runtime_checkable
class MessageLogPort(Protocol):
    """Журнал id сообщений участников — нужен, чтобы зачистить их при бане."""

    async def save(self, chat_id: int, user_id: int, message_id: int) -> None:
        """Запомнить сообщение участника."""
        ...

    async def pop_user_messages(self, chat_id: int, user_id: int) -> list[int]:
        """Вернуть id сообщений участника в чате и стереть их из журнала."""
        ...


@dataclass(frozen=True, slots=True)
class RepositoryBundle:
    """Grouped repositories for application services."""

    chats: ChatRepositoryPort
    threads: ThreadRepositoryPort
    stats: StatsRepositoryPort
    dm: DmRepositoryPort
    delivery_claims: DeliveryClaimPort | None = None
    moderation: ModerationRepositoryPort | None = None
    message_log: MessageLogPort | None = None
