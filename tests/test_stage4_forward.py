"""Stage 4: forward-via-reply transport and real delivery regression checks.

The Telegram edge is loaded with a small SDK shim so no token, Mongo, or
running Telegram process is required. The application relay itself is real.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from application.services.forward_intent import ReplyAction, decide_reply_action, parse_forward_intent
from application.services.reply import ReplyRelayRequest, ReplyService
from ports.repositories import RepositoryBundle
from tests.test_stage2_delivery import ChatRepo, Channel, Dm, Stats, ThreadRepo


def load_telegram_edge():
    """Import the actual forward handler with fake transport *imports* only."""
    class Bot: pass
    class Message: pass

    aiogram = types.ModuleType("aiogram")
    aiogram.Bot = Bot
    aiogram_types = types.ModuleType("aiogram.types")
    aiogram_types.Message = Message
    tg = types.ModuleType("adapters.telegram")
    tg.__path__ = []
    tg.TelegramChannelAdapter = lambda bot: bot.channel
    handlers = types.ModuleType("adapters.telegram.handlers")
    handlers.__path__ = []
    common = types.ModuleType("adapters.telegram.handlers.common")
    common.has_media = lambda msg: bool(getattr(msg, "media", False))
    common.sender_mention = lambda msg: "@sender"
    database = types.ModuleType("database")
    database.db = None
    loaded = {
        "aiogram": aiogram, "aiogram.types": aiogram_types,
        "adapters.telegram": tg, "adapters.telegram.handlers": handlers,
        "adapters.telegram.handlers.common": common, "database": database,
    }
    src = Path(__file__).resolve().parents[1] / "adapters/telegram/handlers/forward_reply.py"
    spec = importlib.util.spec_from_file_location("adapters.telegram.handlers.forward_reply", src)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, loaded):
        spec.loader.exec_module(module)
    return module


def load_main_router(forward_module):
    """Load actual handler logic with stub aiogram decorators (no real polling)."""
    class FakeF:
        def __getattr__(self, name): return self
        def startswith(self, value): return self
        def __and__(self, other): return self
        def __invert__(self): return self

    class FakeRouter:
        def message(self, *args, **kwargs):
            return lambda func: func

    aiogram = types.ModuleType("aiogram")
    aiogram.Bot = object
    aiogram.Router = FakeRouter
    aiogram.F = FakeF()
    aiogram_types = types.ModuleType("aiogram.types")
    aiogram_types.Message = object
    tg = types.ModuleType("adapters.telegram")
    tg.__path__ = []
    tg.TelegramChannelAdapter = lambda bot: bot.channel
    tg.telegram_message_to_attachments = lambda msg: ()
    handlers = types.ModuleType("adapters.telegram.handlers")
    handlers.__path__ = []
    common = types.ModuleType("adapters.telegram.handlers.common")
    common.has_media = lambda msg: bool(getattr(msg, "media", False))
    common.sender_mention = lambda msg: "@sender"
    common.is_real_reply = lambda msg: bool(msg.reply_to_message)
    common.resolve_chat_and_text = AsyncMock(return_value=None)
    group_media = types.ModuleType("adapters.telegram.handlers.group_media")
    group_media.relay_group_media = AsyncMock()
    database = types.ModuleType("database")
    database.db = types.SimpleNamespace(get_repositories=lambda: None)
    modules = {
        "aiogram": aiogram, "aiogram.types": aiogram_types,
        "adapters.telegram": tg, "adapters.telegram.handlers": handlers,
        "adapters.telegram.handlers.common": common,
        "adapters.telegram.handlers.group_media": group_media,
        "adapters.telegram.handlers.forward_reply": forward_module,
        "database": database,
    }
    src = Path(__file__).resolve().parents[1] / "adapters/telegram/handlers/main.py"
    spec = importlib.util.spec_from_file_location("adapters.telegram.handlers.main", src)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class FakeMessage:
    def __init__(self, text="B!", message_id=10, chat_id=-1, *, protected=False, media=False, caption=None):
        self.text = text
        self.caption = caption
        self.message_id = message_id
        self.message_thread_id = 50
        self.chat = types.SimpleNamespace(id=chat_id, type="supergroup")
        self.from_user = types.SimpleNamespace(id=123)
        self.has_protected_content = protected
        self.media = media
        self.responses = []

    async def reply(self, text):
        self.responses.append(text)


class ForwardIntentTests(unittest.TestCase):
    def test_forward_prefixes(self):
        self.assertEqual(parse_forward_intent(" B! ").destination_name, "B")
        self.assertEqual(parse_forward_intent("Two Words! a!b").comment, "a!b")
        self.assertEqual(parse_forward_intent("B! note").comment, "note")

    def test_non_command_reply_does_not_forward(self):
        for text in (None, "", "Thank you", "!", "/help!", "B without separator", "A\nB! hi"):
            self.assertIsNone(parse_forward_intent(text))
        self.assertEqual(decide_reply_action("hello", True), ReplyAction.REPLY)
        self.assertEqual(decide_reply_action("hello", False), ReplyAction.IGNORE)
        self.assertEqual(decide_reply_action("B!", True), ReplyAction.FORWARD)
        self.assertEqual(decide_reply_action("B!", False), ReplyAction.FORWARD)


class ForwardIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_telegram_edge()
        self.chats = ChatRepo()
        self.threads = ThreadRepo()
        self.stats = Stats()
        self.dm = Dm()
        self.channel = Channel()
        self.repos = RepositoryBundle(self.chats, self.threads, self.stats, self.dm)
        self.module.db = types.SimpleNamespace(get_repositories=lambda: self.repos)
        async def lookup(name):
            return self.chats.docs.get(-2) if name == "B" else None
        self.chats.get_chat_by_name = lookup
        self.bot = types.SimpleNamespace(channel=self.channel)

    async def test_forward_reply_creates_header_and_original_with_reply_mapping(self):
        command = FakeMessage(text="B! extra <&>")
        replied = FakeMessage(text="someone else's original", message_id=9)
        handled = await self.module.forward_replied_message(command, self.bot, replied)
        self.assertTrue(handled)
        self.assertEqual([call[0] for call in self.channel.calls], ["text", "forward"])
        self.assertEqual(self.channel.calls[1][2].message_id, "9")
        self.assertIn("&lt;&amp;&gt;", self.channel.calls[0][3])
        self.assertEqual(self.threads.saved[0]["origin_anchor_message_id"], 9)
        sent_ids = self.threads.saved[0]["relay_message_ids"]
        self.assertEqual(len(sent_ids), 2)
        reply = ReplyService(self.repos, self.channel)
        for i, sent_id in enumerate(sent_ids):
            result = await reply.relay_reply(ReplyRelayRequest(-2, 200+i, sent_id, "@other", "response"))
            self.assertTrue(result.delivered)
            self.assertEqual(self.channel.calls[-1][2].message_id, "9")

    async def test_forward_another_bot_message_still_routes(self):
        command = FakeMessage(text="B!")
        source = FakeMessage(text="bot text", message_id=50)
        self.assertTrue(await self.module.forward_replied_message(command, self.bot, source))
        self.assertEqual(self.channel.calls[1][2].message_id, "50")

    async def test_protected_content_not_forwarded(self):
        command = FakeMessage(text="B!")
        source = FakeMessage(message_id=9, protected=True)
        self.assertTrue(await self.module.forward_replied_message(command, self.bot, source))
        self.assertEqual(len(self.channel.calls), 0)
        self.assertIn("защитой Telegram", command.responses[0])

    async def test_reply_with_new_media_cannot_silently_discard_attachment(self):
        command = FakeMessage(text=None, caption="B!", media=True)
        self.assertTrue(await self.module.forward_replied_message(command, self.bot, FakeMessage()))
        self.assertEqual(self.channel.calls, [])
        self.assertIn("без вложения", command.responses[0])

    async def test_unknown_target_in_group_is_silent(self):
        command = FakeMessage(text="Missing! message")
        self.assertFalse(await self.module.forward_replied_message(command, self.bot, FakeMessage()))
        self.assertEqual(command.responses, [])
        self.assertFalse(await self.module.forward_replied_message(
            command, self.bot, FakeMessage(), unknown_as_plain_reply=True
        ))
        self.assertEqual(command.responses, [])
        self.assertEqual(self.channel.calls, [])

    async def test_unknown_target_error_html_escaped(self):
        # The error is still shown in private chats only.
        command = FakeMessage(text="<bad>! msg")
        command.chat.type = "private"
        self.assertTrue(await self.module.forward_replied_message(command, self.bot, FakeMessage()))
        self.assertIn("&lt;bad&gt;", command.responses[0])

    async def test_does_not_forward_without_explicit_command(self):
        command = FakeMessage(text="ordinary reply")
        self.assertFalse(await self.module.forward_replied_message(command, self.bot, FakeMessage()))
        self.assertFalse(self.channel.calls)

    async def test_destination_permission_and_topic_restrictions_still_apply(self):
        chief = self.chats.docs[-3]
        async def lookup(name): return chief if name == "CHIEF" else self.chats.docs[-2]
        self.chats.get_chat_by_name = lookup
        command = FakeMessage(text="CHIEF!")
        self.assertTrue(await self.module.forward_replied_message(command, self.bot, FakeMessage()))
        self.assertEqual(self.channel.calls, [])
        self.assertEqual(self.threads.saved, [])
        command = FakeMessage(text="B!")
        command.message_thread_id = 90
        self.assertTrue(await self.module.forward_replied_message(command, self.bot, FakeMessage()))
        self.assertEqual(self.channel.calls, [])


class HandlerPriorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.forward = load_telegram_edge()
        self.main = load_main_router(self.forward)
        self.main.forward_replied_message = AsyncMock(return_value=True)
        self.main._do_relay_reply = AsyncMock()

        async def get_me():
            return types.SimpleNamespace(id=800)
        self.bot = types.SimpleNamespace(get_me=get_me)

    async def test_reply_to_someone_else_with_target_is_forward(self):
        msg = FakeMessage(text="B!")
        msg.reply_to_message = FakeMessage(message_id=9)
        msg.reply_to_message.from_user = types.SimpleNamespace(id=7)
        await self.main.handle_reply(msg, self.bot)
        self.main.forward_replied_message.assert_awaited_once()
        self.main._do_relay_reply.assert_not_awaited()

    async def test_reply_to_bot_with_target_also_forwards(self):
        msg = FakeMessage(text="B!")
        msg.reply_to_message = FakeMessage(message_id=9)
        msg.reply_to_message.from_user = types.SimpleNamespace(id=800)
        await self.main.handle_reply(msg, self.bot)
        self.main.forward_replied_message.assert_awaited_once()
        self.main._do_relay_reply.assert_not_awaited()

    async def test_regular_reply_to_bot_keeps_conversation(self):
        msg = FakeMessage(text="Thanks for the help")
        msg.reply_to_message = FakeMessage(message_id=9)
        msg.reply_to_message.from_user = types.SimpleNamespace(id=800)
        await self.main.handle_reply(msg, self.bot)
        self.main.forward_replied_message.assert_not_awaited()
        self.main._do_relay_reply.assert_awaited_once()

    async def test_unknown_destination_in_bot_reply_falls_back(self):
        self.main.forward_replied_message.return_value = False
        msg = FakeMessage(text="Great! thanks")
        msg.reply_to_message = FakeMessage(message_id=9)
        msg.reply_to_message.from_user = types.SimpleNamespace(id=800)
        await self.main.handle_reply(msg, self.bot)
        self.main.forward_replied_message.assert_awaited_once()
        self.main._do_relay_reply.assert_awaited_once()

    async def test_plain_reply_to_nonbot_not_relayed(self):
        msg = FakeMessage(text="just a normal reply")
        msg.reply_to_message = FakeMessage(message_id=9)
        msg.reply_to_message.from_user = types.SimpleNamespace(id=9)
        await self.main.handle_reply(msg, self.bot)
        self.main.forward_replied_message.assert_not_awaited()
        self.main._do_relay_reply.assert_not_awaited()

    async def test_media_reply_to_bot_still_uses_reply_service(self):
        msg = FakeMessage(text=None, caption="photo caption", media=True)
        msg.reply_to_message = FakeMessage(message_id=9)
        msg.reply_to_message.from_user = types.SimpleNamespace(id=800)
        await self.main.handle_reply_media(msg, self.bot)
        self.main._do_relay_reply.assert_awaited_once()
        self.main.forward_replied_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
