"""Telegram handler integration with aiogram transport fakes (no API / credentials)."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_site_feedback_A import MemoryRepo


class Magic:
    def __getattr__(self, _): return self
    def startswith(self, _): return self
    def __and__(self, _): return self
    def __invert__(self): return self


class Router:
    def message(self, *filters, **kwargs): return lambda function: function
    def callback_query(self, *filters, **kwargs): return lambda function: function


class Filter:
    pass


class Command:
    def __init__(self, *args, **kwargs): pass


class Message:
    def __init__(self, uid=123, text="", private=True):
        self.from_user = types.SimpleNamespace(
            id=uid, username=f"nick{uid}", full_name=f"User #{uid}")
        self.chat = types.SimpleNamespace(type="private" if private else "supergroup")
        self.text = text
        self.outputs = []

    async def answer(self, text, **kwargs):
        self.outputs.append((text, kwargs))

    async def reply(self, text, **kwargs):
        self.outputs.append((text, kwargs))


class CallbackQuery:
    def __init__(self, data, uid=123, private=True):
        self.data = data
        self.from_user = types.SimpleNamespace(
            id=uid, username=f"nick{uid}", full_name=f"User #{uid}")
        self.message = Message(uid=uid, private=private)
        self.answered = []

    async def answer(self, text="", **kwargs):
        self.answered.append((text, kwargs))


class InlineKeyboardButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data


class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


def load_site_handlers(db_instance):
    fake_aiogram = types.ModuleType("aiogram")
    fake_aiogram.Router = Router
    fake_aiogram.F = Magic()
    filter_module = types.ModuleType("aiogram.filters")
    filter_module.Command = Command
    filter_module.Filter = Filter
    types_module = types.ModuleType("aiogram.types")
    types_module.Message = Message
    types_module.CallbackQuery = CallbackQuery
    types_module.InlineKeyboardButton = InlineKeyboardButton
    types_module.InlineKeyboardMarkup = InlineKeyboardMarkup
    cfg = types.ModuleType("config")
    cfg.settings = types.SimpleNamespace(ADMIN_ID=999, SITE_FEEDBACK_BASE_URL="https://communars.itcheckup.pro")
    db = types.ModuleType("database")
    db.db = db_instance
    handlers = types.ModuleType("adapters.telegram.handlers")
    handlers.__path__ = []
    all_modules = {"aiogram": fake_aiogram, "aiogram.filters": filter_module,
                   "aiogram.types": types_module, "config": cfg,
                   "database": db, "adapters.telegram.handlers": handlers}
    source = Path(__file__).resolve().parents[1] / "adapters/telegram/handlers"
    with patch.dict(sys.modules, all_modules):
        for name in ("site_feedback_ui", "site_feedback", "site_feedback_admin"):
            full = "adapters.telegram.handlers." + name
            spec = importlib.util.spec_from_file_location(full, source / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
        return (sys.modules["adapters.telegram.handlers.site_feedback"],
                sys.modules["adapters.telegram.handlers.site_feedback_admin"],
                sys.modules["adapters.telegram.handlers.site_feedback_ui"])


class FakeBot:
    def __init__(self):
        self.notifications = []
    async def send_message(self, uid, text):
        self.notifications.append((uid, text))


class SiteFeedbackTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo = MemoryRepo()
        self.db = types.SimpleNamespace(get_site_feedback_repository=lambda: self.repo)
        self.user_handler, self.admin_handler, self.ui = load_site_handlers(self.db)
        self.bot = FakeBot()

    async def invoke(self, data, uid=123, private=True):
        call = CallbackQuery(data, uid, private)
        await self.user_handler.site_callback(call)
        return call

    async def create_review(self):
        await self.invoke("sf:review")
        await self.invoke("sf:page:home")
        await self.invoke("sf:type:bug")
        message = Message(123, "Ошибка в отображении каталога")
        self.assertTrue(await self.user_handler.HasFeedbackDraft()(message))
        await self.user_handler.site_draft_text(message)
        self.assertIn("Подтвердите", message.outputs[0][0])
        saved = await self.invoke("sf:submit")
        self.assertIn("сохранено", saved.message.outputs[0][0])
        return next(iter(self.repo.docs.values()))

    async def test_real_flow_and_owner_only_visibility(self):
        item = await self.create_review()
        own = await self.invoke(f"sf:item:{item['_id']}")
        self.assertIn("Ошибка в отображении", own.message.outputs[0][0])
        another = await self.invoke(f"sf:item:{item['_id']}", uid=777)
        self.assertIn("не найдено", another.message.outputs[0][0])
        self.assertNotIn("Ошибка в отображении", another.message.outputs[0][0])

    async def test_page_select_to_link_and_confirm(self):
        await self.invoke("sf:review")
        page = await self.invoke("sf:page:commune_members")
        self.assertIn("slug", page.message.outputs[0][0])
        context = Message(123, "klubnika")
        await self.user_handler.site_draft_text(context)
        self.assertIn("/communes/klubnika/members/", context.outputs[0][0])
        await self.invoke("sf:type:improvement")
        body = Message(123, "Нужен фильтр участников & <b>html</b>")
        await self.user_handler.site_draft_text(body)
        self.assertIn("&lt;b&gt;html&lt;/b&gt;", body.outputs[0][0])

    async def test_normal_dm_handler_not_intercepted_without_draft(self):
        msg = Message(123, "B! Hello")
        self.assertFalse(await self.user_handler.HasFeedbackDraft()(msg))
        await self.invoke("sf:review")
        await self.invoke("sf:page:home")
        await self.invoke("sf:type:bug")
        self.assertTrue(await self.user_handler.HasFeedbackDraft()(msg))
        await self.user_handler.command_cancel(Message(123, "/cancel"))
        self.assertFalse(await self.user_handler.HasFeedbackDraft()(msg))

    async def test_only_private_callbacks(self):
        call = await self.invoke("sf:review", private=False)
        self.assertEqual(self.repo.drafts, {})
        self.assertTrue(call.answered[0][1]["show_alert"])

    async def test_admin_unauthorized_callback(self):
        item = await self.create_review()
        call = CallbackQuery(f"sfa:set:{item['_id']}:accepted", 777)
        await self.admin_handler.admin_callback(call, self.bot)
        self.assertEqual(self.repo.docs[item["submission_key"]]["status"], "new")
        self.assertIn("Недостаточно прав", call.answered[0][0])

    async def test_admin_status_change_and_notification(self):
        item = await self.create_review()
        call = CallbackQuery(f"sfa:set:{item['_id']}:in_review", 999)
        await self.admin_handler.admin_callback(call, self.bot)
        self.assertEqual(self.repo.docs[item["submission_key"]]["status"], "in_review")
        self.assertEqual(self.bot.notifications[0][0], 123)
        self.assertIn("На рассмотрении", self.bot.notifications[0][1])

    async def test_admin_username_search_and_pagination(self):
        item = await self.create_review()
        query = Message(999, "/sitefeedback @nick123")
        await self.admin_handler.admin_command(query)
        self.assertIn("обращений: 1", query.outputs[0][0])
        keyboard = query.outputs[0][1]["reply_markup"].inline_keyboard
        self.assertEqual(keyboard[0][0].callback_data, f"sfa:item:{item['_id']}")

    async def test_admin_no_rights_to_commands(self):
        query = Message(123, "/sitefeedback")
        await self.admin_handler.admin_command(query)
        self.assertIn("только администратору", query.outputs[0][0])

    async def test_bounded_callback_data(self):
        item = await self.create_review()
        callback = f"sfa:set:{item['_id']}:implemented"
        self.assertLessEqual(len(callback.encode()), 64)

    async def test_router_order_in_source(self):
        src = (Path(__file__).resolve().parents[1] /
               "adapters/telegram/handlers/__init__.py").read_text()
        self.assertLess(src.index("router.include_router(site_feedback.router)"),
                        src.index("router.include_router(dm.router)"))
        bot_source = (Path(__file__).resolve().parents[1] / "bot.py").read_text()
        self.assertIn('"callback_query"', bot_source)
