"""Stage 1 regression tests; standard-library only (no Telegram/Mongo required)."""
import asyncio
import unittest

from application.services.delivery_receipt import MAPPING_WARNING, finalize_delivery
from application.services.identity import sender_label_for_chat
from application.services.relay import RelayService, RelayTextRequest
from application.services.reply import ReplyRelayRequest, ReplyService
from application.services.routing import (
    dm_destination_allowed, group_destination_allowed, resolve_destination,
)
from domain.models import ChannelRef, MessageRef
from ports.repositories import RepositoryBundle
from presentation.formatters import (
    chat_relay_header, format_chat_relay_text, format_dm_media_header,
    format_dm_relay_text, format_reply_relay_text,
)


class FakeChats:
    def __init__(self, chats):
        self.chats = {c["chat_id"]: c for c in chats}
        self.migrations = []

    async def get_chat_by_id(self, chat_id):
        return self.chats.get(chat_id)

    async def get_all_chats(self):
        return list(self.chats.values())

    async def update_chat(self, chat_id, update):
        self.migrations.append((chat_id, update))
        return True


class FakeThreads:
    def __init__(self, fails=False):
        self.fails = fails
        self.saved = []
        self.thread = None

    async def save_thread(self, **kwargs):
        if self.fails:
            raise RuntimeError("Mongo unavailable")
        self.saved.append(kwargs)

    async def find_thread_by_relay(self, chat, msg):
        return self.thread

    async def find_thread_by_origin(self, chat, msg):
        return self.thread


class FakeStats:
    def __init__(self, fails=False):
        self.fails = fails
        self.calls = []

    async def increment_stat(self, a, b):
        self.calls.append((a, b))
        if self.fails:
            raise RuntimeError("Stats unavailable")


class FakeDm:
    def __init__(self, anonymous=False, fails=False):
        self.anonymous = anonymous
        self.fails = fails

    async def get_dm_session(self, user_id):
        if self.fails:
            raise RuntimeError("DM privacy lookup unavailable")
        return {"anonymous": self.anonymous}


class FakeChannel:
    def __init__(self):
        self.sent = []
        self.reactions = []
        self.fail_send = None
        self.fail_reaction = False

    async def send_text(self, target, text, reply_to=None, **kwargs):
        if self.fail_send:
            raise self.fail_send
        self.sent.append((target, text, reply_to))
        return MessageRef(ChannelRef("telegram", target.chat_id), 999)

    async def set_reaction(self, ref, emoji):
        self.reactions.append((ref, emoji))
        if self.fail_reaction:
            raise RuntimeError("Reaction not supported")


class MigrationError(Exception):
    migrate_to_chat_id = -900


class Stage1Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = {"chat_id": -1, "name": "Исток", "group": "msk", "is_chief": False}
        self.normal = {"chat_id": -2, "name": "Цель", "group": "msk", "is_chief": False}
        self.chief = {"chat_id": -3, "name": "Главная", "group": "msk", "is_chief": True}
        self.redirect = {**self.normal, "redirect_to": -3}
        self.chats = FakeChats([self.source, self.normal, self.chief])
        self.threads = FakeThreads()
        self.stats = FakeStats()
        self.dm = FakeDm()
        self.channel = FakeChannel()
        self.repositories = RepositoryBundle(self.chats, self.threads, self.stats, self.dm)
        self.relay = RelayService(self.repositories, self.channel)

    def request(self, target=None, origin=None, text="Привет"):
        return RelayTextRequest(
            origin_chat_id=-1 if origin is None else origin,
            origin_message_id=42,
            target_chat=self.normal if target is None else target,
            text=text,
            sender_label="@tester",
            sender_user_id=123,
        )

    async def test_redirect_is_checked_after_resolution(self):
        actual = await resolve_destination(self.chats, self.redirect)
        self.assertEqual(actual["chat_id"], -3)
        self.assertFalse(group_destination_allowed(self.source, self.redirect, actual))
        self.assertFalse(dm_destination_allowed(self.redirect, actual))
        result = await self.relay.relay_group_text(self.request(self.redirect))
        self.assertTrue(result.skipped)
        self.assertFalse(self.channel.sent)

    async def test_normal_group_delivery(self):
        result = await self.relay.relay_group_text(self.request())
        self.assertTrue(result.delivered)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(len(self.threads.saved), 1)
        self.assertEqual(len(self.stats.calls), 1)
        self.assertEqual(len(self.channel.reactions), 1)

    async def test_chief_group_can_send_to_chief(self):
        chief_source = {**self.source, "is_chief": True}
        self.chats.chats[-1] = chief_source
        result = await self.relay.relay_group_text(self.request(self.chief))
        self.assertTrue(result.delivered)

    async def test_no_access_from_special_group(self):
        self.chats.chats[-1] = {**self.source, "group": "special"}
        result = await self.relay.relay_group_text(self.request())
        self.assertFalse(result.delivered)
        self.assertEqual(len(self.channel.sent), 0)

    async def test_dm_privacy_and_redirect(self):
        self.dm.anonymous = True
        blocked = await self.relay.relay_dm_text(self.request(self.redirect, origin=123))
        self.assertTrue(blocked.skipped)
        self.assertEqual(self.channel.sent, [])
        allowed = await self.relay.relay_dm_text(self.request(self.normal, origin=123))
        self.assertTrue(allowed.delivered)
        message = self.channel.sent[-1][1]
        self.assertIn("анонима", message)
        self.assertNotIn("@tester", message)

    async def test_dm_privacy_fail_closed(self):
        self.dm.fails = True
        with self.assertRaises(RuntimeError):
            await self.relay.relay_dm_text(self.request(origin=123))
        self.assertFalse(self.channel.sent)

    async def test_private_reply_identity(self):
        self.dm.anonymous = True
        label = await sender_label_for_chat(self.dm, "private", 123, "@secret")
        self.assertEqual(label, "анонима")
        self.assertEqual(
            await sender_label_for_chat(self.dm, "supergroup", 123, "@secret"), "@secret"
        )
        self.threads.thread = {
            "origin_chat_id": 123, "origin_message_id": 12,
            "relay_chat_id": -2, "relay_message_id": 15,
        }
        result = await ReplyService(self.repositories, self.channel).relay_reply(
            ReplyRelayRequest(123, 88, 12, label, "reply <hello>")
        )
        self.assertTrue(result.delivered)
        body = self.channel.sent[-1][1]
        self.assertNotIn("@secret", body)
        self.assertIn("&lt;hello&gt;", body)

    async def test_send_success_but_mapping_failure_never_retries(self):
        self.threads.fails = True
        result = await self.relay.relay_group_text(self.request())
        self.assertTrue(result.delivered)
        self.assertEqual(result.user_error, MAPPING_WARNING)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(len(self.stats.calls), 1)
        self.assertEqual(len(self.channel.reactions), 1)

    async def test_stats_and_reactions_fail_after_delivery(self):
        self.stats.fails = True
        self.channel.fail_reaction = True
        result = await self.relay.relay_group_text(self.request())
        self.assertTrue(result.delivered)
        self.assertIsNone(result.user_error)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(len(self.threads.saved), 1)

    async def test_dm_mapping_fail_after_success(self):
        self.threads.fails = True
        result = await self.relay.relay_dm_text(self.request(origin=123))
        self.assertTrue(result.delivered)
        self.assertEqual(result.user_error, MAPPING_WARNING)
        self.assertEqual(len(self.channel.sent), 1)

    async def test_reply_mapping_failure_after_send(self):
        self.threads.fails = True
        self.threads.thread = {
            "origin_chat_id": 123, "origin_message_id": 12,
            "relay_chat_id": -2, "relay_message_id": 15,
        }
        result = await ReplyService(self.repositories, self.channel).relay_reply(
            ReplyRelayRequest(123, 88, 12, "@x", "hello")
        )
        self.assertTrue(result.delivered)
        self.assertEqual(result.user_error, MAPPING_WARNING)
        self.assertEqual(len(self.channel.sent), 1)

    async def test_migration_then_mapping_failure_does_not_resend(self):
        self.threads.fails = True
        self.channel.fail_send = MigrationError("migrate to chat")
        original_send = self.channel.send_text
        attempts = []

        async def migrate_once(target, text, **kwargs):
            attempts.append(target.chat_id)
            if len(attempts) == 1:
                raise MigrationError("migrate to chat")
            self.channel.fail_send = None
            return await original_send(target, text, **kwargs)

        self.channel.send_text = migrate_once
        result = await self.relay.relay_group_text(self.request())
        self.assertTrue(result.delivered)
        self.assertEqual(attempts, ["-2", "-900"])
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(result.migrated_to_chat_id, -900)
        self.assertEqual(result.user_error, MAPPING_WARNING)

    def test_escapes_user_content_once(self):
        self.assertIn("&lt;b&gt;", chat_relay_header("<b>Bad", "@a&b"))
        self.assertIn("@a&amp;b", chat_relay_header("<b>Bad", "@a&b"))
        group = format_chat_relay_text("<i>Chat</i>", "@x", "<b>hi & bye</b>")
        self.assertIn("&lt;b&gt;hi &amp; bye&lt;/b&gt;", group)
        self.assertNotIn("<b>hi", group)
        self.assertIn("&lt;i&gt;", format_dm_relay_text("<i>x", "a<b"))
        self.assertIn("&lt;a&gt;", format_dm_media_header("test", "<a>"))
        self.assertIn("&lt;script&gt;", format_reply_relay_text("@x", "<script>"))
        self.assertNotIn("&amp;amp;", group)


if __name__ == "__main__":
    unittest.main()
