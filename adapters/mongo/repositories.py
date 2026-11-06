"""
MongoDB implementations of repository ports.

This adapter wraps the current Database object without changing runtime
handlers yet. Later services can receive RepositoryBundle instead of using
Mongo collections or database.py directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ports.repositories import RepositoryBundle
from adapters.mongo.claims import MongoDeliveryClaims
from adapters.mongo.message_log import MongoMessageLog
from adapters.mongo.moderation import MongoModerationRepository


class MongoChatRepository:
    """Mongo adapter for registered chats."""

    def __init__(self, database: Any):
        self._database = database

    async def get_chat_by_id(self, chat_id: int) -> dict | None:
        return await self._database.get_chat_by_id(chat_id)

    async def get_chat_by_name(self, name: str) -> dict | None:
        return await self._database.get_chat_by_name(name)

    async def get_all_chats(self) -> list[dict]:
        return await self._database.get_all_chats()

    async def get_chats_by_group(self, group: str) -> list[dict]:
        return await self._database.get_chats_by_group(group)

    async def add_chat(self, data: dict) -> bool:
        return await self._database.add_chat(data)

    async def update_chat(self, chat_id: int, update: dict) -> bool:
        return await self._database.update_chat(chat_id, update)

    async def remove_chat(self, chat_id: int) -> bool:
        return await self._database.remove_chat(chat_id)


class MongoThreadRepository:
    """Mongo adapter for relay/reply thread mappings."""

    def __init__(self, database: Any):
        self._database = database

    async def save_thread(
        self,
        origin_chat_id: int,
        origin_message_id: int,
        relay_chat_id: int,
        relay_message_id: int,
        **kwargs,
    ) -> None:
        await self._database.save_thread(
            origin_chat_id, origin_message_id, relay_chat_id, relay_message_id,
            **kwargs,
        )

    async def find_thread_by_relay(
        self,
        relay_chat_id: int,
        relay_message_id: int,
        *, channel: str = "telegram",
    ) -> dict | None:
        return await self._database.find_thread_by_relay(relay_chat_id, relay_message_id, channel=channel)

    async def find_thread_by_origin(
        self,
        origin_chat_id: int,
        origin_message_id: int,
        *, channel: str = "telegram",
    ) -> dict | None:
        return await self._database.find_thread_by_origin(origin_chat_id, origin_message_id, channel=channel)


class MongoStatsRepository:
    """Mongo adapter for relay statistics."""

    def __init__(self, database: Any):
        self._database = database

    async def increment_stat(self, from_chat_id: int, to_chat_id: int) -> None:
        await self._database.increment_stat(from_chat_id, to_chat_id)

    async def get_stats_all(self) -> list[dict]:
        return await self._database.get_stats_all()

    async def get_stats_recent(self, days: int) -> list[dict]:
        return await self._database.get_stats_recent(days)


class MongoDmRepository:
    """Mongo adapter for private-message state."""

    def __init__(self, database: Any):
        self._database = database

    async def is_dm_user_new(self, user_id: int) -> bool:
        return await self._database.is_dm_user_new(user_id)

    async def get_dm_session(self, user_id: int) -> dict | None:
        return await self._database.get_dm_session(user_id)

    async def set_dm_session(self, user_id: int, anonymous: bool) -> None:
        await self._database.set_dm_session(user_id, anonymous)


@dataclass(frozen=True, slots=True)
class MongoRepositoryBundle:
    """Concrete Mongo repository bundle."""

    chats: MongoChatRepository
    threads: MongoThreadRepository
    stats: MongoStatsRepository
    dm: MongoDmRepository
    delivery_claims: MongoDeliveryClaims
    moderation: MongoModerationRepository
    message_log: MongoMessageLog


def create_mongo_repositories(database: Any) -> RepositoryBundle:
    """Create repository bundle over the current Database instance."""
    bundle = MongoRepositoryBundle(
        chats=MongoChatRepository(database),
        threads=MongoThreadRepository(database),
        stats=MongoStatsRepository(database),
        dm=MongoDmRepository(database),
        delivery_claims=MongoDeliveryClaims(database),
        moderation=MongoModerationRepository(database),
        message_log=MongoMessageLog(database),
    )
    return RepositoryBundle(
        chats=bundle.chats,
        threads=bundle.threads,
        stats=bundle.stats,
        dm=bundle.dm,
        delivery_claims=bundle.delivery_claims,
        moderation=bundle.moderation,
        message_log=bundle.message_log,
    )
