"""Stage 3 regression suite, self-contained (requires no Mongo server)."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from adapters.mongo.claims import MongoDeliveryClaims
from adapters.mongo.repositories import create_mongo_repositories
from application.services.delivery import DeliveryPipeline, DeliveryPart, DeliveryPlan
from application.services.delivery_identity import delivery_id
from domain.models import ChannelRef, MessageRef

from tests.test_stage2_delivery import ChatRepo, ThreadRepo, Stats, Dm, Channel
from ports.repositories import RepositoryBundle


def load_database_module():
    """Load real DB code with Motor stub, avoiding external dependency in CI."""
    fake_motor = types.ModuleType("motor")
    fake_asyncio = types.ModuleType("motor.motor_asyncio")
    fake_asyncio.AsyncIOMotorClient = object
    fake_cfg = types.ModuleType("config")
    fake_cfg.settings = types.SimpleNamespace(MONGODB_URI="unused", DB_NAME="test")
    old = {n: sys.modules.get(n) for n in ("motor", "motor.motor_asyncio", "config")}
    try:
        sys.modules.update({"motor": fake_motor, "motor.motor_asyncio": fake_asyncio,
                            "config": fake_cfg})
        spec = importlib.util.spec_from_file_location("lackey_db_stage3_test", Path(__file__).resolve().parents[1] / "database.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name, val in old.items():
            if val is None: sys.modules.pop(name, None)
            else: sys.modules[name] = val


class ClaimCollection:
    """Atomic fake for Mongo's update_one(_id, $setOnInsert, upsert=True)."""
    def __init__(self):
        self.docs = {}
        self.lock = asyncio.Lock()
        self.reject = False

    async def update_one(self, selector, update, upsert=False):
        async with self.lock:
            if self.reject:
                raise OSError("Mongo unreachable")
            key = selector["_id"]
            if key in self.docs:
                if "$set" in update and all(self.docs[key].get(k) == v for k, v in selector.items() if k != "_id"):
                    self.docs[key].update(update["$set"])
                return types.SimpleNamespace(upserted_id=None)
            if not upsert:
                return types.SimpleNamespace(upserted_id=None)
            self.docs[key] = dict(update["$setOnInsert"], _id=key)
            return types.SimpleNamespace(upserted_id=key)

    async def delete_one(self, selector):
        key = selector["_id"]
        if key in self.docs and self.docs[key].get("status") == selector.get("status"):
            del self.docs[key]


class DBCollections:
    def __init__(self):
        self.delivery_claims = ClaimCollection()


class Stage3ClaimsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DBCollections()
        self.chats, self.threads, self.stats, self.dm = ChatRepo(), ThreadRepo(), Stats(), Dm()
        self.channel = Channel()
        self.repo = RepositoryBundle(self.chats, self.threads, self.stats, self.dm, MongoDeliveryClaims(self.db))
        self.pipeline = DeliveryPipeline(self.repo, self.channel)
        self.origin = MessageRef(ChannelRef("telegram", -1), 10)
        self.target = ChannelRef("telegram", -2)

    def plan(self):
        return DeliveryPlan(origin=self.origin, target=self.target,
                            parts=(DeliveryPart("text", text="ping"),))

    async def test_claims_atomic_under_concurrent_delivery(self):
        a, b = await asyncio.gather(self.pipeline.deliver(self.plan()), self.pipeline.deliver(self.plan()))
        self.assertEqual(sum(int(x.delivered) for x in (a, b)), 1)
        self.assertEqual(len(self.channel.calls), 1)
        self.assertEqual(len(self.threads.saved), 1)
        self.assertEqual(self.stats.calls, [(-1, -2)])
        self.assertEqual(list(self.db.delivery_claims.docs.values())[0]["status"], "delivered")

    async def test_repeat_after_pipeline_recreation_still_suppressed(self):
        self.assertTrue((await self.pipeline.deliver(self.plan())).delivered)
        other = DeliveryPipeline(self.repo, self.channel)
        dup = await other.deliver(self.plan())
        self.assertFalse(dup.delivered)
        self.assertIsNone(dup.user_error)
        self.assertEqual(len(self.channel.calls), 1)

    async def test_mongo_failed_claim_must_prevent_send(self):
        self.db.delivery_claims.reject = True
        result = await self.pipeline.deliver(self.plan())
        self.assertFalse(result.delivered)
        self.assertIn("MongoDB", result.user_error)
        self.assertEqual(self.channel.calls, [])

    async def test_send_timeout_is_blocked_from_retry(self):
        self.channel.fail_kind = "text"
        outcome = await self.pipeline.deliver(self.plan())
        self.assertFalse(outcome.delivered)
        self.assertEqual(list(self.db.delivery_claims.docs.values())[0]["status"], "uncertain")
        self.channel.fail_kind = None
        self.assertFalse((await self.pipeline.deliver(self.plan())).delivered)
        self.assertEqual(len(self.channel.calls), 1)

    async def test_partial_forward_does_not_send_second_time(self):
        self.channel.fail_kind = "forward"
        plan = DeliveryPlan(origin=self.origin, target=self.target,
              parts=(DeliveryPart("text", text="header"),
                     DeliveryPart("forward", forward_source=self.origin)))
        first = await self.pipeline.deliver(plan)
        self.assertTrue(first.partial)
        self.assertEqual(list(self.db.delivery_claims.docs.values())[0]["status"], "partial")
        await self.pipeline.deliver(plan)
        self.assertEqual(len(self.channel.calls), 2)

    async def test_migration_exception_releases_only_pre_send_claim(self):
        class Migration(Exception):
            migrate_to_chat_id = -100
        self.channel.send_text = AsyncMock(side_effect=Migration("migrate"))
        out = await self.pipeline.deliver(self.plan())
        self.assertFalse(out.delivered)
        self.assertFalse(self.db.delivery_claims.docs)

    async def test_same_requested_route_dedupes_across_redirect_change(self):
        plan = DeliveryPlan(origin=self.origin, target=ChannelRef("telegram", -3),
                            claim_target=self.target, parts=(DeliveryPart("text", text="hi"),))
        first = await self.pipeline.deliver(plan)
        self.assertTrue(first.delivered)
        changed_redirect = DeliveryPlan(origin=self.origin, target=ChannelRef("telegram", -4),
                            claim_target=self.target, parts=(DeliveryPart("text", text="hi"),))
        self.assertFalse((await self.pipeline.deliver(changed_redirect)).delivered)
        self.assertEqual(len(self.channel.calls), 1)

    async def test_identity_is_platform_qualified_and_order_independent(self):
        x = delivery_id(self.origin, self.target, [10, 11, 12])
        y = delivery_id(self.origin, self.target, [12, 11, 10])
        z = delivery_id(MessageRef(ChannelRef("vk", -1), 10), self.target, [11, 12])
        self.assertEqual(x, y)
        self.assertNotEqual(x, z)
        self.assertNotEqual(x, delivery_id(self.origin, ChannelRef("telegram", -3), [11, 12]))
        self.assertEqual(x, delivery_id(self.origin, ChannelRef("telegram", -2, topic_id=9), [12, 11]))


class Stage3DatabaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.DB = load_database_module().Database
        self.db = self.DB()
        self.db._db = types.SimpleNamespace(threads=types.SimpleNamespace(find_one=AsyncMock()))

    async def test_new_thread_metadata_preserves_legacy_fields(self):
        self.db.threads.insert_one = AsyncMock()
        await self.db.save_thread(-1, 10, -2, 20, relay_message_ids=[20, 21])
        doc = self.db.threads.insert_one.await_args.args[0]
        self.assertEqual((doc["origin_chat_id"], doc["relay_message_id"]), (-1, 20))
        self.assertEqual((doc["origin_channel"], doc["relay_channel"]), ("telegram", "telegram"))
        self.assertEqual(doc["relay_external_chat_id"], "-2")

    async def test_telegram_lookup_filters_legacy_and_new_without_vk_collision(self):
        await self.db.find_thread_by_relay(-2, 22)
        selector = self.db.threads.find_one.await_args.args[0]
        self.assertEqual(selector["relay_chat_id"], -2)
        self.assertIn({"relay_channel": {"$exists": False}}, selector["$and"][0]["$or"])
        await self.db.find_thread_by_relay("-2", "22", channel="vk")
        vk = self.db.threads.find_one.await_args.args[0]
        self.assertEqual(vk["relay_channel"], "vk")
        self.assertEqual(vk["relay_external_chat_id"], "-2")
        self.assertNotIn("relay_chat_id", vk)

    async def test_repository_bundle_has_claims(self):
        repos = create_mongo_repositories(self.db)
        self.assertIsInstance(repos.delivery_claims, MongoDeliveryClaims)

    async def test_stats_recent_merges_legacy_with_daily_and_restores_totals(self):
        def old_agg(pipeline):
            return types.SimpleNamespace(to_list=AsyncMock(return_value=[
                {"from_chat_id": -1, "to_chat_id": -2, "total": 12, "recent": 2}]))
        def new_agg(pipeline):
            return types.SimpleNamespace(to_list=AsyncMock(return_value=[
                {"_id": {"from": -1, "to": -2}, "recent": 3},
                {"_id": {"from": -1, "to": -3}, "recent": 1}]))
        self.db._db.stats = types.SimpleNamespace(aggregate=old_agg,
            find=lambda query: types.SimpleNamespace(to_list=AsyncMock(return_value=[
                {"from_chat_id": -1, "to_chat_id": -2, "total": 15},
                {"from_chat_id": -1, "to_chat_id": -3, "total": 1}])))
        self.db._db.stats_daily = types.SimpleNamespace(aggregate=new_agg)
        rows = await self.db.get_stats_recent(7)
        self.assertEqual([(r["to_chat_id"], r["recent"], r["total"]) for r in rows],
                         [(-2, 5, 15), (-3, 1, 1)])

    async def test_stats_no_longer_pushes_unbounded_timestamps(self):
        self.db._db.stats = types.SimpleNamespace(update_one=AsyncMock())
        self.db._db.stats_daily = types.SimpleNamespace(update_one=AsyncMock())
        await self.db.increment_stat(-1, -2)
        stats_args = self.db._db.stats.update_one.await_args.args
        self.assertEqual(stats_args[1], {"$inc": {"total": 1}})
        daily_args = self.db._db.stats_daily.update_one.await_args.args
        self.assertRegex(daily_args[0]["day"], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
