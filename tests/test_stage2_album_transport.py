"""Smoke test real album-sender function against a fake aiogram transport.

Stub ONLY external SDK classes. No Bot API calls or credentials are used.
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from application.services.delivery import PartialDispatchError
from domain.models import Attachment, AttachmentKind, ChannelRef, MessageRef


class FakeMedia:
    def __init__(self, **values):
        self.values = values


class TelegramBadRequest(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class FakeBot:
    def __init__(self):
        self.calls = []
        self.fail_on = None
        self.id = 500

    async def send_media_group(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on == len(self.calls):
            raise RuntimeError("next batch down")
        if self.fail_on == "reply" and kwargs.get("reply_to_message_id"):
            raise TelegramBadRequest("message to be replied not found")
        result = []
        for _ in kwargs["media"]:
            self.id += 1
            result.append(types.SimpleNamespace(chat=types.SimpleNamespace(id=kwargs["chat_id"]),
                            message_id=self.id, message_thread_id=kwargs.get("message_thread_id")))
        return result


def load_albums():
    pkg = types.ModuleType("aiogram")
    pkg.Bot = FakeBot
    exc = types.ModuleType("aiogram.exceptions")
    exc.TelegramBadRequest = TelegramBadRequest
    atypes = types.ModuleType("aiogram.types")
    for name in ("InputMediaPhoto", "InputMediaVideo", "InputMediaAudio", "InputMediaDocument"):
        setattr(atypes, name, type(name, (FakeMedia,), {}))
    path = Path(__file__).resolve().parents[1] / "adapters/telegram/albums.py"
    with patch.dict(sys.modules, {"aiogram": pkg, "aiogram.types": atypes,
                                  "aiogram.exceptions": exc}):
        spec = importlib.util.spec_from_file_location("stage2_test_album_module", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class AlbumSenderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.albums = load_albums()
        self.bot = FakeBot()
        self.target = ChannelRef("telegram", -2, topic_id=66)
        self.reply_to = MessageRef(self.target, 44)
        self.singles = []

    async def send_one(self, chat_id, topic_id, attachment, caption, reply_id):
        self.singles.append((chat_id, topic_id, attachment, caption, reply_id))
        self.bot.id += 1
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id),
                                     message_id=self.bot.id, message_thread_id=topic_id)

    def photos(self, n):
        return tuple(Attachment(AttachmentKind.PHOTO, external_file_id=f"p{i}") for i in range(n))

    async def test_all_items_get_ids_with_topic_and_reply(self):
        sent = await self.albums.send_album_to_chat(
            self.bot, self.target, self.photos(3), "caption", self.reply_to, self.send_one)
        self.assertEqual(len(sent), 3)
        self.assertEqual(self.bot.calls[0]["chat_id"], -2)
        self.assertEqual(self.bot.calls[0]["message_thread_id"], 66)
        self.assertEqual(self.bot.calls[0]["reply_to_message_id"], 44)
        self.assertEqual(self.bot.calls[0]["media"][0].values["caption"], "caption")
        self.assertNotIn("caption", self.bot.calls[0]["media"][1].values)

    async def test_sends_11_in_two_batches_without_losing_ids(self):
        sent = await self.albums.send_album_to_chat(
            self.bot, self.target, self.photos(11), "caption", None, self.send_one)
        self.assertEqual(len(sent), 11)
        self.assertEqual(len(self.bot.calls), 1)
        self.assertEqual(len(self.singles), 1)
        self.assertIsNone(self.singles[0][3])

    async def test_partial_second_batch_exposes_first_ids(self):
        self.bot.fail_on = 2
        with self.assertRaises(PartialDispatchError) as context:
            await self.albums.send_album_to_chat(
                self.bot, self.target, self.photos(20), "caption", None, self.send_one)
        self.assertEqual(len(context.exception.sent), 10)
        self.assertEqual(len(self.bot.calls), 2)

    async def test_deleted_reply_target_retries_without_reply(self):
        self.bot.fail_on = "reply"
        sent = await self.albums.send_album_to_chat(
            self.bot, self.target, self.photos(2), "caption", self.reply_to, self.send_one)
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(self.bot.calls), 2)
        self.assertNotIn("reply_to_message_id", self.bot.calls[-1])


if __name__ == "__main__":
    unittest.main()
