"""Stage 2 regression checks. No Mongo, aiogram or real Telegram needed."""
import unittest

from application.services.delivery import PARTIAL_WARNING, MAPPING_WARNING
from application.services.relay import RelayService, RelayTextRequest
from application.services.reply import ReplyService, ReplyRelayRequest
from domain.models import Attachment, AttachmentKind, ChannelRef, MessageRef
from ports.repositories import RepositoryBundle


def ref(chat, msg, topic=None):
    return MessageRef(ChannelRef("telegram", chat, topic_id=topic), msg)


class ChatRepo:
    def __init__(self):
        self.docs = {
            -1: {"chat_id": -1, "name": "A", "group": "msk", "is_chief": False, "topic_id": 50},
            -2: {"chat_id": -2, "name": "B", "group": "msk", "is_chief": False, "topic_id": 60},
            -3: {"chat_id": -3, "name": "CHIEF", "group": "msk", "is_chief": True},
        }

    async def get_chat_by_id(self, key):
        return self.docs.get(key)

    async def update_chat(self, key, update):
        if key not in self.docs:
            return False
        old = self.docs.pop(key)
        old.update(update)
        self.docs[old["chat_id"]] = old
        return True


class ThreadRepo:
    def __init__(self):
        self.saved = []
        self.fails = False

    async def save_thread(self, **values):
        if self.fails:
            raise RuntimeError("lost mapping")
        self.saved.append(values)

    async def find_thread_by_relay(self, chat, message):
        for item in reversed(self.saved):
            if item["relay_chat_id"] == chat and message in item.get("relay_message_ids", [item["relay_message_id"]]):
                return item
        return None

    async def find_thread_by_origin(self, chat, message):
        for item in reversed(self.saved):
            if item["origin_chat_id"] == chat and message in item.get("origin_message_ids", [item["origin_message_id"]]):
                return item
        return None


class Stats:
    def __init__(self):
        self.calls = []

    async def increment_stat(self, from_id, to_id):
        self.calls.append((from_id, to_id))


class Dm:
    def __init__(self):
        self.anon = False

    async def get_dm_session(self, user):
        return {"anonymous": self.anon}


class Channel:
    def __init__(self):
        self.id = 99
        self.calls = []
        self.reactions = []
        self.fail_kind = None
        self.fail_after = None

    def record(self, kind, target, reply_to=None, text=None):
        self.calls.append((kind, target, reply_to, text))
        if self.fail_kind == kind:
            raise RuntimeError("send failed")
        self.id += 1
        return ref(int(target.chat_id), self.id, target.topic_id)

    async def send_text(self, target, text, reply_to=None, **_):
        return self.record("text", target, reply_to, text)

    async def send_message(self, message):
        return self.record("media", message.ref.channel_ref, message.reply_to, message.text)

    async def send_album(self, target, attachments, caption=None, reply_to=None):
        self.calls.append(("album", target, reply_to, caption))
        if self.fail_kind == "album":
            raise RuntimeError("album rejected")
        out = []
        for _ in attachments:
            self.id += 1
            out.append(ref(int(target.chat_id), self.id, target.topic_id))
        return tuple(out)

    async def forward_message(self, target, source):
        return self.record("forward", target, source)

    async def copy_message(self, target, source):
        return self.record("copy", target, source)

    async def set_reaction(self, origin, emoji):
        self.reactions.append((origin, emoji))


class Stage2Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chats = ChatRepo()
        self.threads = ThreadRepo()
        self.stats = Stats()
        self.dm = Dm()
        self.channel = Channel()
        self.repos = RepositoryBundle(self.chats, self.threads, self.stats, self.dm)
        self.relay = RelayService(self.repos, self.channel)
        self.reply = ReplyService(self.repos, self.channel)

    def group(self, **changes):
        values = dict(origin_chat_id=-1, origin_message_id=10,
                      origin_topic_id=50, target_chat=self.chats.docs[-2],
                      text="Hello", sender_label="@bob")
        values.update(changes)
        return RelayTextRequest(**values)

    def sticker(self):
        return (Attachment(AttachmentKind.STICKER, external_file_id="file-sticker"),)

    def photos(self, n=3):
        return tuple(Attachment(AttachmentKind.PHOTO, external_file_id=f"p{i}") for i in range(n))

    async def test_forward_maps_header_and_forwarded_item(self):
        result = await self.relay.relay_group(self.group(
            text="Forward <here>", forward_source=ref(-1, 9, 50)))
        self.assertTrue(result.delivered)
        self.assertEqual(len(result.relay_message_ids), 2)
        mapping = self.threads.saved[-1]
        self.assertEqual(mapping["relay_message_ids"], list(result.relay_message_ids))
        self.assertEqual(mapping["origin_anchor_message_id"], 9)
        self.assertEqual(mapping["origin_topic_id"], 50)
        self.assertEqual(mapping["relay_topic_id"], 60)
        self.assertEqual(self.stats.calls, [(-1, -2)])
        for reply_on in result.relay_message_ids:
            self.channel.calls.clear()
            r = await self.reply.relay_reply(ReplyRelayRequest(-2, 200+reply_on, reply_on, "@jane", "yes"))
            self.assertTrue(r.delivered)
            self.assertEqual(self.channel.calls[0][2].message_id, "9")
            self.assertEqual(self.channel.calls[0][2].topic_id, "50")

    async def test_captionless_media_maps_header_and_sticker_and_replies(self):
        r = await self.relay.relay_group(self.group(attachments=self.sticker(), text=""))
        self.assertTrue(r.delivered)
        self.assertEqual(len(r.relay_message_ids), 2)
        self.assertEqual([c[0] for c in self.channel.calls], ["text", "media"])
        for replied in r.relay_message_ids:
            answer = await self.reply.relay_reply(ReplyRelayRequest(-2, 88+replied, replied, "@j", "hi"))
            self.assertTrue(answer.delivered)
            self.assertEqual(answer.target_message_id, 10)

    async def test_sticker_reply_has_two_reply_anchors(self):
        self.threads.saved.append(dict(origin_chat_id=-1, origin_message_id=10,
                                       relay_chat_id=-2, relay_message_id=66,
                                       origin_topic_id=50, relay_topic_id=60))
        r = await self.reply.relay_reply(ReplyRelayRequest(
            -2, 90, 66, "@j", "look", attachments=self.sticker(), current_topic_id=60))
        self.assertTrue(r.delivered)
        self.assertEqual([c[0] for c in self.channel.calls], ["text", "media"])
        self.assertEqual([c[2].message_id for c in self.channel.calls], ["10", "10"])
        self.assertEqual(len(r.delivered_message_ids), 2)
        self.assertEqual(self.threads.saved[-1]["relay_message_ids"], list(r.delivered_message_ids))

    async def test_album_all_source_and_output_ids_mapped(self):
        r = await self.relay.relay_group(self.group(
            attachments=self.photos(), origin_message_ids=(10, 11, 12), text="album"))
        self.assertTrue(r.delivered)
        self.assertEqual(len(r.relay_message_ids), 3)
        mapping = self.threads.saved[-1]
        self.assertEqual(mapping["origin_message_ids"], [10, 11, 12])
        self.assertEqual(mapping["relay_message_ids"], list(r.relay_message_ids))
        self.assertEqual(len(self.stats.calls), 1)
        self.assertEqual(len(self.channel.reactions), 1)
        rr = await self.reply.relay_reply(ReplyRelayRequest(-1, 77, 12, "@user", "more", current_topic_id=50))
        self.assertTrue(rr.delivered)
        self.assertEqual(rr.target_message_id, r.relay_message_ids[0])
        self.assertEqual(self.channel.calls[-1][2].topic_id, "60")

    async def test_album_reply_all_output_ids_mapped(self):
        self.threads.saved.append(dict(origin_chat_id=-1, origin_message_id=10,
                                       relay_chat_id=-2, relay_message_id=66,
                                       origin_topic_id=50, relay_topic_id=60))
        r = await self.reply.relay_reply(ReplyRelayRequest(
            -2, 80, 66, "@j", "images", attachments=self.photos(),
            current_topic_id=60, current_message_ids=(80, 81, 82)))
        self.assertTrue(r.delivered)
        self.assertEqual(len(r.delivered_message_ids), 3)
        self.assertEqual(self.channel.calls[0][0], "album")
        self.assertEqual(self.channel.calls[0][2].message_id, "10")
        self.assertEqual(self.threads.saved[-1]["origin_message_ids"], [80, 81, 82])

    async def test_dm_media_respects_anonymity(self):
        self.dm.anon = True
        r = await self.relay.relay_dm(self.group(origin_chat_id=123,
                          origin_topic_id=None, sender_user_id=123, attachments=self.photos(1)))
        self.assertTrue(r.delivered)
        self.assertIn("анонима", self.channel.calls[-1][3])
        self.assertNotIn("@bob", self.channel.calls[-1][3])

    async def test_forward_redirect_to_chief_blocked(self):
        target = {**self.chats.docs[-2], "redirect_to": -3}
        r = await self.relay.relay_group(self.group(
            target_chat=target, forward_source=ref(-1, 9)))
        self.assertTrue(r.skipped)
        self.assertEqual(self.channel.calls, [])

    async def test_group_input_is_bound_to_configured_topic(self):
        r = await self.relay.relay_group(self.group(origin_topic_id=777))
        self.assertTrue(r.skipped)
        self.assertFalse(self.channel.calls)

    async def test_replies_preserve_original_topic_after_setting_change(self):
        r = await self.relay.relay_group(self.group())
        self.chats.docs[-1]["topic_id"] = 300
        rr = await self.reply.relay_reply(ReplyRelayRequest(
            -2, 88, r.relay_message_id, "@other", "reply", current_topic_id=60))
        self.assertTrue(rr.delivered)
        self.assertEqual(self.channel.calls[-1][1].topic_id, "50")

    async def test_legacy_mapping_still_resolves(self):
        self.threads.saved.append(dict(origin_chat_id=-1, origin_message_id=5,
                                       relay_chat_id=-2, relay_message_id=6))
        r = await self.reply.relay_reply(ReplyRelayRequest(-2, 77, 6, "@u", "old"))
        self.assertTrue(r.delivered)
        self.assertEqual(r.target_message_id, 5)
        self.assertEqual(self.channel.calls[-1][1].topic_id, "50")

    async def test_partial_forward_persists_header_without_retry(self):
        self.channel.fail_kind = "forward"
        r = await self.relay.relay_group(self.group(forward_source=ref(-1, 9)))
        self.assertTrue(r.delivered)
        self.assertTrue(r.partial)
        self.assertIn(PARTIAL_WARNING, r.user_error)
        self.assertEqual(len(r.relay_message_ids), 1)
        self.assertEqual(len(self.threads.saved), 1)
        self.assertEqual([c[0] for c in self.channel.calls], ["text", "forward"])
        self.assertEqual(len(self.channel.reactions), 0)

    async def test_after_send_mapping_failure_no_duplicate(self):
        self.threads.fails = True
        r = await self.relay.relay_group(self.group(attachments=self.sticker(), text=""))
        self.assertTrue(r.delivered)
        self.assertEqual(r.user_error, MAPPING_WARNING)
        self.assertEqual(len(self.channel.calls), 2)
        self.assertEqual(len(self.stats.calls), 1)

    async def test_failure_before_send_no_records(self):
        self.channel.fail_kind = "text"
        r = await self.relay.relay_group(self.group())
        self.assertFalse(r.delivered)
        self.assertFalse(self.threads.saved)
        self.assertFalse(self.stats.calls)

    async def test_dm_to_chief_blocked(self):
        r = await self.relay.relay_dm(self.group(origin_chat_id=123, origin_topic_id=None,
                      target_chat=self.chats.docs[-3], attachments=self.photos(1)))
        self.assertTrue(r.skipped)
        self.assertFalse(self.channel.calls)


if __name__ == "__main__":
    unittest.main()
