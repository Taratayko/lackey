"""Stage 5: DM reply-forward with anonymity, mapping and access regression tests.

Uses real handler/service code with fake Telegram transport and Mongo ports.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from application.services.reply import ReplyRelayRequest, ReplyService
from domain.models import ChannelRef, MessageRef
from ports.repositories import RepositoryBundle
from tests.test_stage2_delivery import ChatRepo, Channel, Dm, Stats, ThreadRepo
from tests.test_stage4_forward import FakeMessage, load_main_router, load_telegram_edge


class DmForwardEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handler = load_telegram_edge()
        self.main = load_main_router(self.handler)
        self.chats = ChatRepo()
        self.threads = ThreadRepo()
        self.stats = Stats()
        self.dm = Dm()
        self.channel = Channel()
        self.repos = RepositoryBundle(self.chats, self.threads, self.stats, self.dm)
        self.handler.db = types.SimpleNamespace(get_repositories=lambda: self.repos)
        self.main.db = self.handler.db

        async def get_chat_by_name(name):
            return next((d for d in self.chats.docs.values() if d["name"] == name), None)
        self.chats.get_chat_by_name = get_chat_by_name

        async def get_me():
            return types.SimpleNamespace(id=800)
        self.bot = types.SimpleNamespace(get_me=get_me, channel=self.channel)

    def dm_message(self, text="B!", *, replied_to_bot=True, protected=False):
        message = FakeMessage(text=text, chat_id=123)
        message.chat.type = "private"
        message.message_thread_id = None
        replied = FakeMessage(text="the original from DM", message_id=9, chat_id=123, protected=protected)
        replied.chat.type = "private"
        replied.message_thread_id = None
        replied.from_user = types.SimpleNamespace(id=800 if replied_to_bot else 123)
        message.reply_to_message = replied
        return message

    async def test_dm_reply_forward_and_return_to_original_dm_message(self):
        command = self.dm_message(text="B! note <private&>")
        await self.main.handle_reply(command, self.bot)
        self.assertFalse(command.responses)
        self.assertEqual([call[0] for call in self.channel.calls], ["text", "forward"])
        self.assertIn("ЛС", self.channel.calls[0][3])
        self.assertIn("&lt;private&amp;&gt;", self.channel.calls[0][3])
        saved = self.threads.saved[0]
        self.assertEqual(saved["origin_chat_id"], 123)
        self.assertEqual(saved["origin_message_id"], 10)
        self.assertEqual(saved["origin_anchor_message_id"], 9)
        self.assertEqual(saved["origin_topic_id"], None)
        self.assertEqual(len(saved["relay_message_ids"]), 2)
        self.assertEqual(self.stats.calls, [(123, -2)])
        for relay_id in saved["relay_message_ids"]:
            result = await ReplyService(self.repos, self.channel).relay_reply(
                ReplyRelayRequest(-2, 200 + relay_id, relay_id, "@reply", "response", current_topic_id=60)
            )
            self.assertTrue(result.delivered)
            self.assertEqual(result.target_chat_id, 123)
            self.assertEqual(result.target_message_id, 9)
            self.assertIsNone(self.channel.calls[-1][1].topic_id)

    async def test_dm_reply_to_own_message_forwards(self):
        command = self.dm_message(replied_to_bot=False)
        await self.main.handle_reply(command, self.bot)
        self.assertEqual([call[0] for call in self.channel.calls], ["text", "forward"])

    async def test_dm_anon_uses_copy_without_forward_attribution(self):
        self.dm.anon = True
        command = self.dm_message(text="B! secret", replied_to_bot=False)
        await self.main.handle_reply(command, self.bot)
        self.assertEqual([call[0] for call in self.channel.calls], ["text", "copy"])
        self.assertIn("анонима", self.channel.calls[0][3])
        self.assertNotIn("@sender", self.channel.calls[0][3])
        self.assertNotIn("@sender", str(self.threads.saved))
        self.assertEqual(len(self.threads.saved[0]["relay_message_ids"]), 2)

    async def test_dm_chief_and_redirect_blocked(self):
        command = self.dm_message(text="CHIEF!")
        await self.main.handle_reply(command, self.bot)
        self.assertEqual(self.channel.calls, [])
        self.assertEqual(self.threads.saved, [])
        self.assertIn("запрещена правилами", command.responses[0])
        self.chats.docs[-2]["redirect_to"] = -3
        redirected = self.dm_message(text="B!")
        await self.main.handle_reply(redirected, self.bot)
        self.assertEqual(self.channel.calls, [])
        self.assertIn("запрещена правилами", redirected.responses[0])

    async def test_dm_protected_message_is_refused(self):
        command = self.dm_message(protected=True)
        await self.main.handle_reply(command, self.bot)
        self.assertEqual(self.channel.calls, [])
        self.assertIn("защитой Telegram", command.responses[0])

    async def test_dm_plain_reply_still_uses_reply_service(self):
        # Bot's DM message #9 is a relay of B's #77. No B! prefix -> normal reply.
        self.threads.saved.append(dict(
            origin_chat_id=-2, origin_message_id=77,
            relay_chat_id=123, relay_message_id=9,
            origin_topic_id=60, relay_topic_id=None,
        ))
        command = self.dm_message(text="ordinary DM reply")
        command.sticker = None
        command.video_note = None
        await self.main.handle_reply(command, self.bot)
        self.assertEqual([call[0] for call in self.channel.calls], ["text"])
        self.assertEqual(self.channel.calls[0][1].chat_id, "-2")
        self.assertEqual(self.channel.calls[0][2].message_id, "77")

    async def test_dm_text_without_reply_not_changed(self):
        # Only explicit reply commands use the forward handler.
        command = self.dm_message()
        command.reply_to_message = None
        await self.main.handle_reply(command, self.bot)
        self.assertEqual(self.channel.calls, [])

    async def test_dm_duplicate_claim_suppresses_resend(self):
        # Existing persisted claims work the same way for a private chat.
        class Claims:
            def __init__(self): self.keys = set()
            async def claim(self, key, origin, target):
                if key in self.keys: return False
                self.keys.add(key)
                return True
            async def complete(self, *args, **kwargs): return None
        self.repos = RepositoryBundle(self.chats, self.threads, self.stats, self.dm, Claims())
        self.handler.db = types.SimpleNamespace(get_repositories=lambda: self.repos)
        self.main.db = self.handler.db
        command = self.dm_message()
        await self.main.handle_reply(command, self.bot)
        await self.main.handle_reply(command, self.bot)
        self.assertEqual(len(self.channel.calls), 2)
        self.assertEqual(len(self.threads.saved), 1)


class TelegramCopyAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_copy_message_returns_message_id_and_destination(self):
        class Bot:
            def __init__(self): self.calls = []
            async def copy_message(self, **kwargs):
                self.calls.append(kwargs)
                return types.SimpleNamespace(message_id=707)
        fake_aiogram = types.ModuleType("aiogram")
        fake_aiogram.Bot = Bot
        module_path = Path(__file__).resolve().parents[1] / "adapters/telegram/message_transfer.py"
        spec = importlib.util.spec_from_file_location("test_message_transfer", module_path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"aiogram": fake_aiogram}):
            spec.loader.exec_module(module)
        bot = Bot()
        target = ChannelRef("telegram", -2, topic_id=60)
        source = MessageRef(ChannelRef("telegram", 123), 9)
        result = await module.copy_existing_message(bot, target, source)
        self.assertEqual(result.chat_id, "-2")
        self.assertEqual(result.message_id, "707")
        self.assertEqual(result.topic_id, "60")
        self.assertEqual(bot.calls, [{"from_chat_id": 123, "message_id": 9,
                                     "chat_id": -2, "message_thread_id": 60}])


if __name__ == "__main__":
    unittest.main()
