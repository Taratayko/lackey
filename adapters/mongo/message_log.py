"""Mongo-журнал сообщений участников для зачистки при бане.

Bot API не умеет отдавать историю сообщений пользователя, поэтому бот сам
запоминает message_id входящих сообщений в группах. Записи живут
``MESSAGE_LOG_TTL_DAYS`` суток (TTL-индекс) — удалить можно только то, что бот видел.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MESSAGE_LOG_TTL_DAYS = 7
_MAX_PER_BAN = 5000


class MongoMessageLog:
    """Коллекция ``user_messages``: одна запись на сообщение."""

    def __init__(self, database: Any):
        self._database = database

    @property
    def _col(self):
        return self._database._db.user_messages

    async def ensure_indexes(self) -> None:
        await self._col.create_index([("chat_id", 1), ("user_id", 1)])
        await self._col.create_index(
            "created_at", expireAfterSeconds=MESSAGE_LOG_TTL_DAYS * 86400,
        )

    async def save(self, chat_id: int, user_id: int, message_id: int) -> None:
        await self._col.insert_one({
            "chat_id": chat_id,
            "user_id": user_id,
            "message_id": message_id,
            "created_at": datetime.now(timezone.utc),
        })

    async def pop_user_messages(self, chat_id: int, user_id: int) -> list[int]:
        query = {"chat_id": chat_id, "user_id": user_id}
        docs = await self._col.find(query).sort("message_id", -1).to_list(length=_MAX_PER_BAN)
        await self._col.delete_many(query)
        return [int(doc["message_id"]) for doc in docs]
