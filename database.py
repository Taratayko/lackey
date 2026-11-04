"""
Слой работы с MongoDB.

Коллекции:
  chats       — зарегистрированные беседы
  threads     — цепочки пересланных сообщений (для reply-диалога)

Схема документа chats:
{
  _id: ObjectId,
  chat_id: int,                     # Telegram chat_id (отрицательный для групп)
  name: str,                        # Основное название (напр. "Твари")
  slug: str,                        # Нижний регистр без пробелов для матчинга ("твари")
  aliases: [str],                   # Альтернативные названия (aka)
  group: str,                       # "msk" | "spb" | "special"
  is_chief: bool,                   # Главный чат группы?
  topic_id: int | None,             # message_thread_id для форум-чатов
  link: str | None,                 # t.me/xxx
  description: str | None,          # доп. описание для /list
}

Схема документа threads:
{
  _id: ObjectId,
  # Оригинальное сообщение отправителя
  origin_chat_id: int,
  origin_message_id: int,
  # Сообщение, отправленное ботом в целевой чат
  relay_chat_id: int,
  relay_message_id: int,
  origin_channel: "telegram" | "vk" | "web",   # Stage 3
  relay_channel: "telegram" | "vk" | "web",
  origin_external_chat_id: str,
  relay_external_chat_id: str,
  origin_message_ids: [int | str],              # Stage 2
  relay_message_ids: [int | str],
}

Stage 3 additional collections:
  delivery_claims — unique _id per logical source + requested destination
  stats_daily     — bounded per-day counters; legacy stats.timestamps untouched
"""
from motor.motor_asyncio import AsyncIOMotorClient
from config import settings


class Database:
    def __init__(self):
        self._client = None
        self._db = None

    async def connect(self):
        self._client = AsyncIOMotorClient(settings.MONGODB_URI)
        self._db = self._client[settings.DB_NAME]
        # Индексы
        await self._db.chats.create_index("chat_id", unique=True)
        await self._db.chats.create_index("slug")
        await self._db.chats.create_index("aliases")
        # Stage 3: indexed lookup by chat+message, including multi-ID albums.
        # Non-unique: one origin message may be sent to several chats.
        for side in ("origin", "relay"):
            await self._db.threads.create_index([
                (f"{side}_chat_id", 1), (f"{side}_message_id", 1),
            ])
            await self._db.threads.create_index([
                (f"{side}_chat_id", 1), (f"{side}_message_ids", 1),
            ])
            await self._db.threads.create_index([
                (f"{side}_channel", 1), (f"{side}_external_chat_id", 1),
                (f"{side}_message_ids", 1),
            ])
        # _id in delivery_claims is already unique: atomic claim before send.
        await self._db.delivery_claims.create_index("status")
        await self._db.delivery_claims.create_index("created_at")
        from adapters.mongo.site_feedback import SiteFeedbackRepository
        self._site_feedback_repo = SiteFeedbackRepository(self._db)
        await self._site_feedback_repo.ensure_indexes()
        await self._db.stats_daily.create_index(
            [("from_chat_id", 1), ("to_chat_id", 1), ("day", 1)], unique=True
        )

    @property
    def chats(self):
        return self._db.chats

    @property
    def threads(self):
        return self._db.threads

    @property
    def delivery_claims(self):
        return self._db.delivery_claims

    def get_site_feedback_repository(self):
        """MongoDB-backed site feedback, initialized in connect()."""
        return self._site_feedback_repo

    def get_repositories(self):
        """Create Mongo repository bundle for application services."""
        if not hasattr(self, "_repositories"):
            from adapters.mongo import create_mongo_repositories
            self._repositories = create_mongo_repositories(self)
        return self._repositories

    # ── Чаты ──────────────────────────────────────────────────────────────────

    async def get_chat_by_id(self, chat_id: int) -> dict | None:
        return await self.chats.find_one({"chat_id": chat_id})

    async def get_chat_by_name(self, name: str) -> dict | None:
        """Поиск чата по основному имени или алиасу (регистронезависимо)."""
        slug = name.strip().lower()
        return await self.chats.find_one({
            "$or": [
                {"slug": slug},
                {"aliases": slug}
            ]
        })

    async def get_all_chats(self) -> list[dict]:
        return await self.chats.find().to_list(length=None)

    async def get_chats_by_group(self, group: str) -> list[dict]:
        return await self.chats.find({"group": group}).to_list(length=None)

    async def add_chat(self, data: dict) -> bool:
        """Добавить чат. Возвращает True если добавлен, False если уже существует."""
        try:
            await self.chats.insert_one(data)
            return True
        except Exception:
            return False

    async def update_chat(self, chat_id: int, update: dict) -> bool:
        result = await self.chats.update_one({"chat_id": chat_id}, {"$set": update})
        return result.modified_count > 0

    async def remove_chat(self, chat_id: int) -> bool:
        result = await self.chats.delete_one({"chat_id": chat_id})
        return result.deleted_count > 0

    # ── Цепочки (threads) ─────────────────────────────────────────────────────

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
    ):
        """One document per logical delivery; all visible Telegram IDs are aliases."""
        await self.threads.insert_one({
            "origin_channel": origin_channel,
            "relay_channel": relay_channel,
            "origin_external_chat_id": str(origin_chat_id),
            "relay_external_chat_id": str(relay_chat_id),
            "origin_chat_id": origin_chat_id,
            "origin_message_id": origin_message_id,
            "origin_message_ids": origin_message_ids or [origin_message_id],
            "origin_anchor_message_id": origin_anchor_message_id or origin_message_id,
            "origin_topic_id": origin_topic_id,
            "relay_chat_id": relay_chat_id,
            "relay_message_id": relay_message_id,
            "relay_message_ids": relay_message_ids or [relay_message_id],
            "relay_topic_id": relay_topic_id,
        })

    async def find_thread_by_relay(
        self, relay_chat_id: int | str, relay_message_id: int | str,
        *, channel: str = "telegram",
    ) -> dict | None:
        """Legacy records without a channel are Telegram-only."""
        return await self._find_thread("relay", relay_chat_id, relay_message_id, channel)

    async def find_thread_by_origin(
        self, origin_chat_id: int | str, origin_message_id: int | str,
        *, channel: str = "telegram",
    ) -> dict | None:
        return await self._find_thread("origin", origin_chat_id, origin_message_id, channel)

    async def _find_thread(self, side: str, chat_id, message_id, channel: str):
        if channel == "telegram":
            selector = {
                f"{side}_chat_id": int(chat_id),
                "$and": [
                    {"$or": [
                        {f"{side}_channel": "telegram"},
                        {f"{side}_channel": {"$exists": False}},
                    ]},
                    {"$or": [
                        {f"{side}_message_id": int(message_id)},
                        {f"{side}_message_ids": int(message_id)},
                    ]},
                ],
            }
        else:
            selector = {
                f"{side}_channel": channel,
                f"{side}_external_chat_id": str(chat_id),
                "$or": [
                    {f"{side}_message_id": str(message_id)},
                    {f"{side}_message_ids": str(message_id)},
                ],
            }
        return await self.threads.find_one(selector)

    # ── Статистика ────────────────────────────────────────────────────────────

    async def increment_stat(self, from_chat_id: int, to_chat_id: int):
        """Total is permanent; recent activity uses bounded daily counters.

        Existing timestamp arrays are not extended. stats_daily is additive;
        old timestamps still count in get_stats_recent without migration.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        await self._db.stats.update_one(
            {"from_chat_id": from_chat_id, "to_chat_id": to_chat_id},
            {"$inc": {"total": 1}}, upsert=True,
        )
        await self._db.stats_daily.update_one(
            {"from_chat_id": from_chat_id, "to_chat_id": to_chat_id, "day": day},
            {"$inc": {"total": 1}}, upsert=True,
        )

    async def get_stats_all(self) -> list[dict]:
        return await self._db.stats.find().to_list(length=None)

    async def get_stats_recent(self, days: int) -> list[dict]:
        from datetime import datetime, timezone, timedelta
        if days <= 0:
            return []
        since = datetime.now(timezone.utc) - timedelta(days=days)
        # Since daily buckets are calendar days, include the boundary day.
        # Counts at that boundary are approximate up to one day; legacy
        # timestamps retain their exact time filtering.
        boundary = since.strftime("%Y-%m-%d")
        old = await self._db.stats.aggregate([
            {"$project": {
                "from_chat_id": 1, "to_chat_id": 1, "total": 1,
                "recent": {"$size": {"$filter": {
                    "input": {"$ifNull": ["$timestamps", []]},
                    "as": "t", "cond": {"$gte": ["$$t", since]},
                }}},
            }},
            {"$match": {"recent": {"$gt": 0}}},
        ]).to_list(length=None)
        daily = await self._db.stats_daily.aggregate([
            {"$match": {"day": {"$gte": boundary}}},
            {"$group": {"_id": {"from": "$from_chat_id", "to": "$to_chat_id"},
                        "recent": {"$sum": "$total"}}},
        ]).to_list(length=None)
        pairs: dict[tuple[int, int], dict] = {}
        for item in old:
            key = (item["from_chat_id"], item["to_chat_id"])
            pairs[key] = {"from_chat_id": key[0], "to_chat_id": key[1],
                          "recent": item["recent"], "total": item.get("total", 0)}
        for item in daily:
            key = (item["_id"]["from"], item["_id"]["to"])
            if key not in pairs:
                pairs[key] = {"from_chat_id": key[0], "to_chat_id": key[1],
                              "recent": 0, "total": 0}
            pairs[key]["recent"] += item["recent"]
        if pairs:
            source_ids = list({src for src, _ in pairs})
            totals = await self._db.stats.find(
                {"from_chat_id": {"$in": source_ids}}
            ).to_list(length=None)
            for record in totals:
                key = (record["from_chat_id"], record["to_chat_id"])
                if key in pairs:
                    pairs[key]["total"] = record.get("total", 0)
        return sorted(pairs.values(), key=lambda item: item["recent"], reverse=True)

    # ── ЛС пользователи ───────────────────────────────────────────────────────

    async def is_dm_user_new(self, user_id: int) -> bool:
        existing = await self._db.dm_users.find_one({"user_id": user_id})
        if existing:
            return False
        from datetime import datetime, timezone
        await self._db.dm_users.insert_one({
            "user_id": user_id,
            "first_seen": datetime.now(timezone.utc),
        })
        return True

    async def get_dm_session(self, user_id: int) -> dict | None:
        return await self._db.dm_sessions.find_one({"user_id": user_id})

    async def set_dm_session(self, user_id: int, anonymous: bool):
        await self._db.dm_sessions.update_one(
            {"user_id": user_id},
            {"$set": {"anonymous": anonymous}},
            upsert=True,
        )


db = Database()
