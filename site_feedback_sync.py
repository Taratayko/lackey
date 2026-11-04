"""Outbound-only SiteFeedback synchronization: Lackey Mongo -> PHP/MySQL over HTTPS.

Run as a separate service. Never exposes Telegram/MongoDB to the Internet.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import urlsplit

import aiohttp

from domain.site_feedback import STATUS_NAMES, STATUS_NEXT

LOG = logging.getLogger(__name__)


def encode_time(value):
    return value.isoformat() if isinstance(value, datetime) else value


def serialize_page(row):
    return {"key": row["_id"], "title": row["title"], "route": row["route"],
            "access": row["access"], "visible": bool(row.get("visible", True)),
            "sort_order": row.get("sort_order", 0)}


def serialize_item(row):
    author = row.get("author") or {}
    return {
        "id": str(row["_id"]), "revision": int(row.get("revision", 0)),
        "kind": row.get("kind", ""), "page_key": row.get("page_key"),
        "page_title": row.get("page_title"), "context_slug": row.get("context_slug"),
        "feedback_type": row.get("feedback_type"), "title": row.get("title", ""),
        "body": row.get("body", ""), "status": row["status"],
        "created_at": encode_time(row.get("created_at")),
        "updated_at": encode_time(row.get("updated_at")),
        "decision_note": row.get("decision_note", ""),
        "author": {
            "telegram_user_id": author.get("telegram_user_id"),
            "username": author.get("username_snapshot", ""),
            "display_name": author.get("display_name_snapshot", ""),
        },
        "history": [{
            "action": event.get("action", ""), "from_status": event.get("from_status"),
            "to_status": event.get("to_status"), "comment": event.get("comment", ""),
            "created_at": encode_time(event.get("created_at")),
        } for event in row.get("history", [])[-50:]],
    }


class SiteSyncClient:
    """TLS is mandatory. An HTTP redirect is never followed with credentials."""

    def __init__(self, url: str, token: str, session):
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or not parts.path.endswith('/sync/v1')
                or len(token) < 32):
            raise ValueError("Expected a HTTPS site sync endpoint and 32+ character secret")
        self.base = url.rstrip("/")
        self.token = token
        self.session = session

    async def call(self, method: str, endpoint: str, payload=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        async with self.session.request(
            method, self.base + '/' + endpoint + '/', headers=headers, json=payload,
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Feedback sync HTTP {response.status} at {endpoint}")
            data = await response.json(content_type='application/json')
            if not isinstance(data, dict):
                raise RuntimeError("Feedback sync returned an invalid document")
            return data


class SiteSyncEngine:
    def __init__(self, repo, client, notify=None):
        self.repo, self.client, self.notify = repo, client, notify

    async def snapshot(self):
        pages = [serialize_page(p) for p in await self.repo.pages(include_admin=True)]
        # Mongo _id pagination: stable across updates, unlike offset pagination.
        last_id = None
        while True:
            selector = {"_id": {"$gt": last_id}} if last_id is not None else {}
            records = await self.repo.db.site_feedback_items.find(selector).sort("_id", 1).limit(20).to_list(20)
            if not records:
                break
            await self.client.call('POST', 'snapshot', {
                'pages': pages, 'items': [serialize_item(item) for item in records],
                'aliases': [],
            })
            pages = []
            last_id = records[-1]['_id']
        # Always sync pages even when no feedback has been submitted yet.
        if pages:
            await self.client.call('POST', 'snapshot', {'pages': pages, 'items': [], 'aliases': []})
        await self._aliases()

    async def _aliases(self):
        aliases = []
        cursor = self.repo.db.site_feedback_users.find({}, {"seen_usernames": 1})
        async for row in cursor:
            for username in row.get('seen_usernames', []):
                if isinstance(username, str) and re.fullmatch(r'[a-z0-9_]{5,32}', username.lower()):
                    aliases.append({'telegram_user_id': row['_id'], 'username': username.lower()})
                    if len(aliases) == 100:
                        await self.client.call('POST', 'snapshot', {'pages': [], 'items': [], 'aliases': aliases})
                        aliases = []
        if aliases:
            await self.client.call('POST', 'snapshot', {'pages': [], 'items': [], 'aliases': aliases})

    async def apply_action(self, action):
        action_id = int(action['id'])
        marker = f"site:{action_id}"
        item = await self.repo.get_item(str(action['feedback_id']))
        if item is None:
            return 'conflict', 'item_missing'
        if any(event.get('sync_action_id') == marker for event in item.get('history', [])):
            return 'applied', 'already_applied'
        current = item['status']
        target = action['target_status']
        if (current != action['expected_status'] or target not in STATUS_NEXT.get(current, set())
                or not isinstance(action.get('note'), str) or len(action['note']) > 1000):
            return 'conflict', 'status_changed_or_invalid'
        actor = 'web:' + str(action['actor'])[:48]
        changed = await self.repo.change_status(item, target, actor, action['note'], marker)
        if not changed:
            latest = await self.repo.get_item(str(action['feedback_id']))
            if latest and any(e.get('sync_action_id') == marker for e in latest.get('history', [])):
                return 'applied', 'already_applied'
            return 'conflict', 'concurrent_status_change'
        if self.notify is not None:
            try:
                await self.notify(await self.repo.get_item(str(action['feedback_id'])))
            except Exception:
                LOG.exception('Author notification failed for %s', marker)
        return 'applied', 'applied'

    async def actions(self):
        response = await self.client.call('GET', 'actions')
        for action in response.get('actions', []):
            try:
                state, reason = await self.apply_action(action)
            except Exception:
                LOG.exception('Could not apply site action %s', action.get('id'))
                # Do not acknowledge ambiguous Mongo errors: retry and use marker.
                break
            await self.client.call('POST', 'ack', {'id': int(action['id']), 'state': state, 'reason': reason})

    async def cycle(self):
        await self.snapshot()
        await self.actions()
        await self.snapshot()  # immediately reflect accepted decisions back to the site


async def notify_author(item):
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from config import settings
    user_id = (item.get('author') or {}).get('telegram_user_id')
    if not isinstance(user_id, int):
        return
    message = (f"Статус обращения #{str(item['_id'])[-6:]}: "
               f"{STATUS_NAMES.get(item['status'], item['status'])}. Подробнее: /communesite")
    session = (AiohttpSession(proxy=settings.telegram_proxy_url)
               if settings.telegram_proxy_url else AiohttpSession())
    async with Bot(token=settings.BOT_TOKEN, session=session) as bot:
        await bot.send_message(user_id, message)


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from adapters.mongo.site_feedback import SiteFeedbackRepository
    from config import settings

    if not 5 <= settings.SITE_FEEDBACK_SYNC_INTERVAL <= 3600:
        raise ValueError('SITE_FEEDBACK_SYNC_INTERVAL must be 5..3600 seconds')
    mongo = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000)
    await mongo.admin.command('ping')
    repo = SiteFeedbackRepository(mongo[settings.DB_NAME])
    await repo.ensure_indexes()
    async with aiohttp.ClientSession() as http:
        client = SiteSyncClient(settings.SITE_FEEDBACK_SYNC_URL, settings.SITE_FEEDBACK_SYNC_TOKEN, http)
        engine = SiteSyncEngine(repo, client, notify_author)
        LOG.info('SiteFeedback sync started (outbound HTTPS only)')
        try:
            while True:
                try:
                    await engine.cycle()
                except Exception:
                    LOG.exception('SiteFeedback sync cycle failed (will retry)')
                await asyncio.sleep(settings.SITE_FEEDBACK_SYNC_INTERVAL)
        finally:
            mongo.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(main())
