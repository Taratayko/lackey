"""MongoDB atomic idempotency guard for Telegram/VK/Web deliveries.

Claim documents intentionally have no automatic TTL: ambiguity after a process
crash must never turn into an automatic second send. Operators can inspect
and remove a verified undelivered claim manually after checking Telegram.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from domain.models import ChannelRef, MessageRef


class MongoDeliveryClaims:
    def __init__(self, database: Any) -> None:
        self._database = database

    async def claim(self, key: str, origin: MessageRef, target: ChannelRef) -> bool:
        now = datetime.now(timezone.utc)
        # $setOnInsert is atomic by _id. No read-then-write race.
        result = await self._database.delivery_claims.update_one(
            {"_id": key},
            {"$setOnInsert": {
                "status": "dispatching", "created_at": now, "updated_at": now,
                "origin_channel": origin.channel, "origin_chat_id": origin.chat_id,
                "origin_message_id": origin.message_id,
                "relay_channel": target.channel.value, "relay_chat_id": target.chat_id,
            }},
            upsert=True,
        )
        return result.upserted_id is not None

    async def complete(self, key: str, *, delivered: bool, partial: bool,
                       relay_ids: tuple[str, ...]) -> None:
        status = ("partial" if partial else "delivered") if delivered else "uncertain"
        await self._database.delivery_claims.update_one(
            {"_id": key, "status": "dispatching"},
            {"$set": {"status": status, "updated_at": datetime.now(timezone.utc),
                      "relay_message_ids": list(relay_ids)}},
        )

    async def release_for_migration(self, key: str) -> None:
        """Allowed only for Telegram's explicit MigrateToChat pre-send error."""
        await self._database.delivery_claims.delete_one(
            {"_id": key, "status": "dispatching"}
        )
