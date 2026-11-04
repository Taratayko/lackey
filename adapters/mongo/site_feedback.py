"""MongoDB persistence for site feedback. Separate from message relay collections."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from domain.site_feedback import PAGES


def utcnow():
    return datetime.now(timezone.utc)


class SiteFeedbackRepository:
    def __init__(self, database):
        self.db = database

    async def ensure_indexes(self):
        db = self.db
        await db.site_feedback_items.create_index("submission_key", unique=True)
        await db.site_feedback_items.create_index([("_id", 1)])
        await db.site_feedback_items.create_index([("author.telegram_user_id", 1), ("created_at", -1)])
        await db.site_feedback_items.create_index([("status", 1), ("created_at", -1)])
        await db.site_feedback_items.create_index([("page_key", 1), ("created_at", -1)])
        await db.site_feedback_users.create_index("username_current")
        await db.site_feedback_users.create_index("seen_usernames")
        await db.site_feedback_drafts.create_index("updated_at", expireAfterSeconds=7 * 86400)
        for page in PAGES:
            await db.site_feedback_pages.update_one({"_id": page.key}, {
                "$setOnInsert": {
                    "title": page.title, "route": page.route, "access": page.access,
                    "visible": True, "sort_order": page.order, "created_at": utcnow(),
                }
            }, upsert=True)

    async def pages(self, include_admin: bool = False):
        selector = {"visible": True}
        if not include_admin:
            selector["access"] = "public"
        return await self.db.site_feedback_pages.find(selector).sort("sort_order", 1).to_list(None)

    async def page(self, key: str):
        return await self.db.site_feedback_pages.find_one({"_id": key, "visible": True})

    async def save_draft(self, user_id: int, data: dict):
        draft = dict(data)
        draft["updated_at"] = utcnow()
        await self.db.site_feedback_drafts.replace_one({"_id": user_id}, {"_id": user_id, **draft}, upsert=True)

    async def draft(self, user_id: int):
        return await self.db.site_feedback_drafts.find_one({"_id": user_id})

    async def clear_draft(self, user_id: int):
        await self.db.site_feedback_drafts.delete_one({"_id": user_id})

    async def update_user(self, user_id: int, username: str | None, display_name: str):
        uname = (username or "").strip().lstrip("@").lower()
        update = {"$set": {
            "username_current": uname, "display_name_current": display_name[:150],
            "last_seen_at": utcnow(),
        }, "$setOnInsert": {"first_seen_at": utcnow()}}
        if uname:
            update["$addToSet"] = {"seen_usernames": uname}
        await self.db.site_feedback_users.update_one({"_id": user_id}, update, upsert=True)

    async def user_ids_for_username(self, username: str) -> list[int]:
        name = username.lower().strip().lstrip("@")
        if not name:
            return []
        rows = await self.db.site_feedback_users.find({"seen_usernames": name}, {"_id": 1}).to_list(100)
        return [row["_id"] for row in rows]

    async def create_item(self, document: dict):
        """Retry-safe insert with unique submission_key (draft UUID)."""
        key = document["submission_key"]
        try:
            await self.db.site_feedback_items.update_one(
                {"submission_key": key}, {"$setOnInsert": document}, upsert=True,
            )
        except DuplicateKeyError:
            pass
        return await self.db.site_feedback_items.find_one({"submission_key": key})

    async def get_item(self, item_id: str):
        try:
            oid = ObjectId(item_id)
        except (InvalidId, ValueError, TypeError):
            return None
        return await self.db.site_feedback_items.find_one({"_id": oid})

    async def items(self, selector: dict, offset: int = 0, limit: int = 5):
        return await self.db.site_feedback_items.find(selector).sort(
            [("created_at", -1), ("_id", -1)]
        ).skip(max(0, offset)).limit(min(max(1, limit), 20)).to_list(None)

    async def count(self, selector: dict):
        return await self.db.site_feedback_items.count_documents(selector)

    async def change_status(self, item: dict, status: str, admin_id: int | str, note: str = "",
                            sync_action_id: str | None = None):
        event = {
            "action": "status", "actor_user_id": admin_id,
            "from_status": item["status"], "to_status": status,
            "comment": note, "created_at": utcnow(),
        }
        if sync_action_id:
            event["sync_action_id"] = sync_action_id
        selector = {"_id": item["_id"], "status": item["status"]}
        if sync_action_id:
            selector["history.sync_action_id"] = {"$ne": sync_action_id}
        changed = await self.db.site_feedback_items.update_one(
            selector,
            {"$set": {"status": status, "decision_by": admin_id,
                      "decision_note": note, "updated_at": utcnow()},
             "$push": {"history": event}, "$inc": {"revision": 1}},
        )
        return bool(changed.modified_count)

    async def add_note(self, item: dict, admin_id: int, note: str):
        event = {"action": "note", "actor_user_id": admin_id,
                 "from_status": item["status"], "to_status": item["status"],
                 "comment": note, "created_at": utcnow()}
        await self.db.site_feedback_items.update_one({"_id": item["_id"]}, {
            "$set": {"decision_note": note, "decision_by": admin_id, "updated_at": utcnow()},
            "$push": {"history": event}, "$inc": {"revision": 1},
        })
