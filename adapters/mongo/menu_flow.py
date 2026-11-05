"""MongoDB-хранилище шагов меню «В гости» / «Вписаться».

Состояние короткоживущее: документ сам удаляется через ``TTL_SECONDS``
после последнего изменения, поэтому брошенные сценарии не копятся и не
перехватывают обычные сообщения в ЛС.
"""
from __future__ import annotations

from datetime import datetime, timezone

TTL_SECONDS = 20 * 60


class MongoMenuFlowRepository:
    def __init__(self, database) -> None:
        self._col = database.menu_flows

    async def ensure_indexes(self) -> None:
        await self._col.create_index("user_id", unique=True)
        await self._col.create_index("updated_at", expireAfterSeconds=TTL_SECONDS)

    async def get(self, user_id: int) -> dict | None:
        return await self._col.find_one({"user_id": user_id})

    async def save(self, user_id: int, data: dict) -> None:
        doc = {k: v for k, v in data.items() if k not in {"_id", "user_id", "updated_at"}}
        doc["user_id"] = user_id
        doc["updated_at"] = datetime.now(timezone.utc)
        await self._col.replace_one({"user_id": user_id}, doc, upsert=True)

    async def delete(self, user_id: int) -> None:
        await self._col.delete_one({"user_id": user_id})
