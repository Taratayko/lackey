"""Mongo-журнал активных банов и мутов.

Telegram остаётся источником истины (реальные ограничения живут там). Журнал
нужен только для того, чего Bot API не умеет: найти пользователя по @username
для /unban и /unmute и показать /banlist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class MongoModerationRepository:
    """Коллекция ``moderation_actions``: по одной записи на (chat_id, user_id, kind)."""

    def __init__(self, database: Any):
        self._database = database

    @property
    def _col(self):
        return self._database._db.moderation_actions

    async def ensure_indexes(self) -> None:
        await self._col.create_index(
            [("chat_id", 1), ("user_id", 1), ("kind", 1)], unique=True,
        )
        await self._col.create_index([("chat_id", 1), ("kind", 1), ("username_lower", 1)])

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
        await self._col.update_one(
            {"chat_id": chat_id, "user_id": user_id, "kind": kind},
            {"$set": {
                "username": username,
                "username_lower": username.lower() if username else None,
                "full_name": full_name,
                "actor_user_id": actor_user_id,
                "reason": reason,
                "until": until,
                "created_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

    async def remove(self, chat_id: int, user_id: int, kind: str) -> None:
        await self._col.delete_one({"chat_id": chat_id, "user_id": user_id, "kind": kind})

    @staticmethod
    def _active_filter(chat_id: int, kind: str) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "chat_id": chat_id,
            "kind": kind,
            "$or": [{"until": None}, {"until": {"$gt": now}}],
        }

    async def find_by_username(self, chat_id: int, username: str, kind: str) -> dict | None:
        query = self._active_filter(chat_id, kind)
        query["username_lower"] = username.strip().lstrip("@").lower()
        return await self._col.find_one(query)

    async def list_active(self, chat_id: int, kind: str) -> list[dict]:
        cursor = self._col.find(self._active_filter(chat_id, kind)).sort("created_at", 1)
        return await cursor.to_list(length=500)
