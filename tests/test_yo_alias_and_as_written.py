"""е/ё не различаются при обращении к коммуне; текст пересылается как написан."""
from __future__ import annotations

import asyncio
import os
import re
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "1")

from application.services.admin import AdminService  # noqa: E402
from domain.names import fold_name, same_name, yo_insensitive_regex  # noqa: E402
from presentation.formatters import format_chat_relay_text  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _matches(doc: dict, cond: dict) -> bool:
    ((field, expected),) = cond.items()
    value = doc.get(field)
    values = value if isinstance(value, list) else [value]
    if isinstance(expected, dict):
        return any(isinstance(v, str) and re.search(expected["$regex"], v) for v in values)
    return expected in values


class FakeChats:
    """Минимальная имитация find_one с $or / точным значением / $regex."""

    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, query):
        for doc in self.docs:
            if any(_matches(doc, cond) for cond in query["$or"]):
                return doc
        return None


class NamesTest(unittest.TestCase):
    def test_fold_and_same_name(self):
        self.assertEqual(fold_name("  Всё Просто "), "все просто")
        self.assertTrue(same_name("Всё просто", "все ПРОСТО"))
        self.assertFalse(same_name("все", "всю"))

    def test_regex_escapes_special_characters(self):
        pattern = yo_insensitive_regex("a.b (ё)")
        self.assertTrue(re.search(pattern, "a.b (е)"))
        self.assertTrue(re.search(pattern, "a.b (ё)"))
        self.assertFalse(re.search(pattern, "aXb (е)"))


class LookupTest(unittest.TestCase):
    def setUp(self):
        from database import Database
        self.db = Database()
        self.db._db = types.SimpleNamespace(chats=FakeChats([
            {"chat_id": -1, "slug": "всё просто", "aliases": ["простые"]},
            {"chat_id": -2, "slug": "ёлка", "aliases": ["ёжик"]},
        ]))

    def find(self, name):
        doc = run(self.db.get_chat_by_name(name))
        return doc["chat_id"] if doc else None

    def test_yo_and_ye_are_the_same(self):
        self.assertEqual(self.find("Всё просто"), -1)
        self.assertEqual(self.find("Все просто"), -1)
        self.assertEqual(self.find("ВСЕ ПРОСТО"), -1)
        self.assertEqual(self.find("елка"), -2)
        self.assertEqual(self.find("Ёжик"), -2)
        self.assertEqual(self.find("ежик"), -2)

    def test_plain_alias_and_unknown(self):
        self.assertEqual(self.find("Простые"), -1)
        self.assertIsNone(self.find("несуществующая"))
        self.assertIsNone(self.find(""))

    def test_exact_match_wins_over_folded(self):
        self.db._db.chats.docs.append({"chat_id": -3, "slug": "все просто", "aliases": []})
        self.assertEqual(self.find("все просто"), -3)
        self.assertEqual(self.find("всё просто"), -1)


class FakeChatRepo:
    def __init__(self, doc):
        self.doc = doc

    async def get_chat_by_id(self, chat_id):
        return self.doc

    async def update_chat(self, chat_id, update):
        self.doc.update(update)
        return True


class AliasCommandsTest(unittest.TestCase):
    def setUp(self):
        self.doc = {"chat_id": -1, "slug": "всё просто", "aliases": ["простые"]}
        repos = types.SimpleNamespace(chats=FakeChatRepo(self.doc))
        self.admin = AdminService(repos, owner_user_id=1, regional_cities=())

    def add(self, alias):
        return run(self.admin.add_alias(1, -1, f"/addalias {alias}"))

    def test_alias_equal_to_slug_up_to_yo_is_rejected(self):
        self.assertIn("уже есть", self.add("Все просто").text)
        self.assertEqual(self.doc["aliases"], ["простые"])

    def test_duplicate_alias_up_to_yo_is_rejected(self):
        self.add("ёжики")
        self.assertIn("уже есть", self.add("ежики").text)
        self.assertEqual(self.doc["aliases"], ["простые", "ёжики"])

    def test_new_alias_is_added(self):
        self.assertIn("добавлен", self.add("легкие").text)
        self.assertIn("легкие", self.doc["aliases"])

    def test_remove_alias_ignores_yo(self):
        self.add("ёжики")
        self.assertIn("удалён", run(self.admin.remove_alias(1, -1, "/removealias ежики")).text)
        self.assertEqual(self.doc["aliases"], ["простые"])


class AsWrittenTest(unittest.TestCase):
    def test_resolved_text_keeps_alias_as_written(self):
        import adapters.telegram.handlers.common as common

        async def fake_lookup(name):
            return {"chat_id": -1, "name": "Всё просто"} if name.lower() in {"всё просто", "простые"} else None

        with patch.object(common, "db", types.SimpleNamespace(get_chat_by_name=fake_lookup)):
            chat, text = run(common.resolve_chat_and_text("Всё просто! Хай!"))
            self.assertEqual(chat["chat_id"], -1)
            self.assertEqual(text, "Всё просто! Хай!")
            _, text = run(common.resolve_chat_and_text("Простые! Хай!"))
            self.assertEqual(text, "Простые! Хай!")
            self.assertIsNone(run(common.resolve_chat_and_text("Неизвестно! Хай!")))

    def test_relay_line_shows_arrow_with_alias(self):
        line = format_chat_relay_text("Зиккурат", "@user", "Всё просто! Хай!")
        self.assertTrue(line.endswith("<b>↳ </b>Всё просто! Хай!"))


if __name__ == "__main__":
    unittest.main()
