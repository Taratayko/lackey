"""Меню «В гости» / «Вписаться» по ТЗ (lakey-tz.md): логика, доставка и хендлеры.

Mongo и Telegram подменены fake-объектами; сервисы, формат и хендлеры — настоящие.
"""
from __future__ import annotations

import os
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("ADMIN_ID", "1")

from application.services.menu_flow import MenuFlowError, MenuFlowService, MenuRequestRelay
from application.services import AdminService
from domain.menu_flow import MAX_DAYS, parse_days, plural_days
from ports.repositories import RepositoryBundle
from tests.test_stage2_delivery import Channel, Dm, Stats, ThreadRepo


class FlowRepo:
    def __init__(self):
        self.docs = {}

    async def get(self, user_id):
        doc = self.docs.get(user_id)
        return dict(doc) if doc else None

    async def save(self, user_id, data):
        self.docs[user_id] = {**data, "user_id": user_id}

    async def delete(self, user_id):
        self.docs.pop(user_id, None)


def commune(chat_id, name, group, **extra):
    return {"chat_id": chat_id, "name": name, "group": group, "is_chief": False, **extra}


class Chats:
    def __init__(self):
        self.docs = {c["chat_id"]: c for c in (
            commune(-1, "Бестиарий", "msk", aliases=["твари"], link="https://t.me/b"),
            commune(-2, "Могильный Платон", "msk"),
            commune(-3, "Главная", "msk", is_chief=True),
            commune(-4, "Тайная", "msk", hidden=True),
            commune(-5, "Зиккурат", "spb"),
            commune(-6, "Знамя", "spb", is_chief=True),
            commune(-7, "Пассад", "vlk"),
            commune(-8, "Сетевики", "special", link="https://t.me/commesh"),
        )}

    async def get_chat_by_id(self, chat_id):
        return self.docs.get(chat_id)

    async def get_chats_by_group(self, group):
        return [c for c in self.docs.values() if c["group"] == group]

    async def update_chat(self, *_):
        return True


class PureRulesTests(unittest.TestCase):
    def test_plural_days(self):
        expected = {1: "день", 2: "дня", 4: "дня", 5: "дней", 11: "дней", 12: "дней",
                    14: "дней", 21: "день", 22: "дня", 25: "дней", 60: "дней"}
        for n, word in expected.items():
            self.assertEqual(plural_days(n), word, n)

    def test_parse_days(self):
        self.assertEqual(parse_days(" 4 "), 4)
        for bad in ("", "0", "-1", "4.5", "три", str(MAX_DAYS + 1), "1000"):
            with self.assertRaises(ValueError, msg=bad):
                parse_days(bad)


class OtherDayRulesTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_date(self):
        from domain.menu_flow import MAX_DATE_LEN, parse_date
        self.assertEqual(parse_date("  15 \n ноября "), "15 ноября")
        for bad in ("", "   ", "x" * (MAX_DATE_LEN + 1)):
            with self.assertRaises(ValueError, msg=bad):
                parse_date(bad)

    def test_stage_order_skips_date_unless_other_day(self):
        from domain.menu_flow import next_stage, previous_stage
        self.assertEqual(next_stage("stay", "when", "today"), "days")
        self.assertEqual(next_stage("stay", "when", "other"), "date")
        self.assertEqual(next_stage("stay", "date", "other"), "days")
        self.assertEqual(previous_stage("stay", "days", "today"), "when")
        self.assertEqual(previous_stage("stay", "days", "other"), "date")
        self.assertEqual(previous_stage("stay", "date", "other"), "when")

    async def test_service_other_day_and_back(self):
        flows, svc = FlowRepo(), MenuFlowService(FlowRepo(), Chats())
        await svc.begin(1, "stay", 100)
        await svc.choose_city(1, "msk")
        await svc.choose_commune(1, -1)
        self.assertEqual((await svc.choose_when(1, "other"))["stage"], "date")
        with self.assertRaises(MenuFlowError):
            await svc.accept_date(1, "")
        self.assertEqual((await svc.accept_date(1, "в пятницу"))["stage"], "days")
        self.assertEqual((await svc.back(1))["stage"], "date")
        self.assertEqual((await svc.back(1))["stage"], "when")
        flow = await svc.choose_when(1, "today")                   # передумал: дата сброшена
        self.assertEqual((flow["stage"], flow["date"]), ("days", None))
        await svc.accept_days(1, "3")
        _, _, body = await svc.finish_text(1, "привет")
        self.assertEqual(body, "привет")

    async def test_guest_cannot_choose_other_day(self):
        svc = MenuFlowService(FlowRepo(), Chats())
        await svc.begin(1, "guest", 100)
        await svc.choose_city(1, "msk")
        await svc.choose_commune(1, -1)
        with self.assertRaises(MenuFlowError):
            await svc.choose_when(1, "other")

    def test_stay_keyboard_has_other_day_guest_does_not(self):
        from adapters.telegram.menu_keyboards import when_kb
        stay = [[b.text for b in r] for r in when_kb("stay").inline_keyboard]
        self.assertEqual(stay, [["Сегодня", "Завтра", "Послезавтра"], ["В другой день", "Назад"]])
        self.assertEqual([[b.text for b in r] for r in when_kb("guest").inline_keyboard],
                         [["Сегодня", "Завтра", "Послезавтра", "Назад"]])
        data = when_kb("stay").inline_keyboard[1][0].callback_data
        self.assertEqual(data, "lk:when:other")

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flows, self.chats = FlowRepo(), Chats()
        self.svc = MenuFlowService(self.flows, self.chats)

    async def test_communes_exclude_hidden_and_chief_and_other_groups(self):
        names = [c["name"] for c in await self.svc.communes_for_city("msk")]
        self.assertEqual(names, ["Бестиарий", "Могильный Платон"])
        self.assertEqual([c["name"] for c in await self.svc.communes_for_city("spb")], ["Зиккурат"])

    async def test_guest_flow_order_and_back(self):
        await self.svc.begin(1, "guest", 100)
        await self.svc.choose_city(1, "msk")
        await self.svc.choose_commune(1, -1)
        flow = await self.svc.choose_when(1, "today")
        self.assertEqual(flow["stage"], "text")        # у «В гости» нет шага «дней»
        self.assertEqual((await self.svc.back(1))["stage"], "when")
        self.assertEqual((await self.svc.back(1))["stage"], "commune")
        self.assertEqual((await self.svc.back(1))["stage"], "city")
        self.assertIsNone(await self.svc.back(1))        # с первого экрана — в меню
        self.assertIsNone(await self.svc.get(1))

    async def test_stay_flow_has_days_step(self):
        await self.svc.begin(1, "stay", 100)
        await self.svc.choose_city(1, "spb")
        await self.svc.choose_commune(1, -5)
        self.assertEqual((await self.svc.choose_when(1, "tomorrow"))["stage"], "days")
        with self.assertRaises(MenuFlowError):
            await self.svc.accept_days(1, "много")
        self.assertEqual((await self.svc.get(1))["stage"], "days")   # остались на шаге
        self.assertEqual((await self.svc.accept_days(1, "4"))["stage"], "text")
        self.assertEqual((await self.svc.back(1))["stage"], "days")

    async def test_rejects_wrong_stage_foreign_commune_and_chief(self):
        await self.svc.begin(1, "guest", 100)
        with self.assertRaises(MenuFlowError):
            await self.svc.choose_commune(1, -1)        # сперва город
        await self.svc.choose_city(1, "spb")
        for bad in (-1, -6, -4, 999):                   # Москва, главная, скрытая, чужая
            with self.assertRaises(MenuFlowError):
                await self.svc.choose_commune(1, bad)
        with self.assertRaises(MenuFlowError):
            await self.svc.choose_city(1, "spb")        # шаг уже пройден

    async def test_finish_text_validates_and_keeps_flow_for_retry(self):
        await self.svc.begin(1, "guest", 100)
        await self.svc.choose_city(1, "msk")
        await self.svc.choose_commune(1, -1)
        await self.svc.choose_when(1, "today")
        for bad in ("   ", "x" * 2001):
            with self.assertRaises(MenuFlowError):
                await self.svc.finish_text(1, bad)
        flow, chat, body = await self.svc.finish_text(1, "  привет  ")
        self.assertEqual((chat["name"], body), ("Бестиарий", "привет"))
        self.assertIsNotNone(await self.svc.get(1))      # удаляет только вызывающий код


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chats, self.threads = Chats(), ThreadRepo()
        self.dm, self.channel = Dm(), Channel()
        repos = RepositoryBundle(self.chats, self.threads, Stats(), self.dm)
        self.relay = MenuRequestRelay(repos, self.channel)

    async def send(self, flow, chat_id=-1, text="привет, можно прийти к 17:30?", message_id=10):
        return await self.relay.send_request(
            flow, self.chats.docs[chat_id], text, origin_chat_id=777,
            origin_message_id=message_id, user_id=5, visible_name="@thetrtk")

    async def test_guest_message_matches_tz_and_is_replyable(self):
        result = await self.send({"kind": "guest", "when": "today"})
        self.assertTrue(result.delivered)
        kind, target, _, text = self.channel.calls[0]
        self.assertEqual((kind, int(target.chat_id)), ("text", -1))
        self.assertEqual(
            text, "<b>@thetrtk хочет прийти сегодня в гости</b>\n<b>↳ </b>привет, можно прийти к 17:30?")
        saved = self.threads.saved[0]                     # mapping для reply-цепочки
        self.assertEqual((saved["origin_chat_id"], saved["relay_chat_id"]), (777, -1))
        self.assertEqual(self.channel.reactions[0][1], "✍")

    async def test_stay_message_declension(self):
        for days, word in ((1, "день"), (4, "дня"), (5, "дней")):
            self.channel.calls.clear()
            await self.send({"kind": "stay", "when": "after", "days": days}, message_id=20 + days)
            self.assertIn(f"хочет вписаться послезавтра на {days} {word}</b>", self.channel.calls[0][3])

    async def test_anon_mode_does_not_apply_to_requests(self):
        self.dm.anon = True
        await self.send({"kind": "guest", "when": "tomorrow"})
        self.assertTrue(self.channel.calls[0][3].startswith("<b>@thetrtk хочет прийти завтра в гости</b>"))

    async def _chain_reply_label(self, start):
        """Старт диалога -> ответ коммуны -> ответ пользователя при /anon; вернуть шапку 3-го сообщения."""
        from application.services.reply import ReplyRelayRequest, ReplyService
        await start()
        relay_id = int(self.threads.saved[0]["relay_message_id"])
        reply = ReplyService(RepositoryBundle(self.chats, self.threads, Stats(), self.dm), self.channel)
        await reply.relay_reply(ReplyRelayRequest(
            current_chat_id=-1, current_message_id=500, replied_message_id=relay_id, sender_label="@bob"))
        to_user = int(self.threads.saved[1]["relay_message_id"])
        self.dm.anon = True
        await reply.relay_reply(ReplyRelayRequest(
            current_chat_id=777, current_message_id=11, replied_message_id=to_user,
            sender_label="анонима", open_label="@thetrtk", text="ок"))
        return self.channel.calls[-1][3], self.threads.saved

    async def test_dialog_after_request_stays_open_even_with_anon(self):
        text, saved = await self._chain_reply_label(
            lambda: self.send({"kind": "stay", "when": "today", "days": 3}))
        self.assertIn("от @thetrtk", text)
        self.assertNotIn("анонима", text)
        self.assertTrue(all(t.get("open_identity") for t in saved))   # флаг наследуется по цепочке

    async def test_plain_dm_dialog_stays_anonymous_with_anon(self):
        from application.services.relay import RelayTextRequest

        async def plain():
            from application.services.relay import RelayService
            self.dm.anon = True
            plain_relay = RelayService(
                RepositoryBundle(self.chats, self.threads, Stats(), self.dm), self.channel)
            await plain_relay.relay_dm(RelayTextRequest(
                origin_chat_id=777, origin_message_id=10, target_chat=self.chats.docs[-1],
                text="привет", sender_label="@thetrtk", sender_user_id=5))
        text, saved = await self._chain_reply_label(plain)
        self.assertIn("от анонима", text)
        self.assertFalse(any(t.get("open_identity") for t in saved))

    async def test_html_in_user_text_is_escaped(self):
        await self.send({"kind": "guest", "when": "today"}, text="<b>&</b>")
        self.assertIn("&lt;b&gt;&amp;&lt;/b&gt;", self.channel.calls[0][3])

    async def test_chief_commune_is_refused(self):
        result = await self.send({"kind": "guest", "when": "today"}, chat_id=-3)
        self.assertTrue(result.skipped and not result.delivered)
        self.assertFalse(self.channel.calls)

    async def test_duplicate_update_does_not_send_twice(self):
        class Claims:
            seen = set()
            async def claim(self, key, *_):
                if key in self.seen:
                    return False
                self.seen.add(key); return True
            async def complete(self, *a, **k): pass
            async def release_for_migration(self, key): pass
        repos = RepositoryBundle(self.chats, self.threads, Stats(), self.dm, Claims())
        relay = MenuRequestRelay(repos, self.channel)
        args = dict(origin_chat_id=777, origin_message_id=10, user_id=5, visible_name="@u")
        flow = {"kind": "guest", "when": "today"}
        await relay.send_request(flow, self.chats.docs[-1], "a", **args)
        second = await relay.send_request(flow, self.chats.docs[-1], "a", **args)
        self.assertFalse(second.delivered)
        self.assertEqual(len(self.channel.calls), 1)


class ListAndKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_screen_is_shared_rich_list(self):
        repos = RepositoryBundle(Chats(), ThreadRepo(), Stats(), Dm())
        text = await AdminService(repos, 1, [("vlk", "Владивостокские", "⛵️")]).render_full_list_text()
        self.assertIn("<h2>к КоммуСети подключены:</h2>", text)   # настоящий Rich-заголовок
        self.assertIn('<a href="https://t.me/b">Бестиарий</a>', text)
        self.assertIn("<code>твари</code>", text)
        self.assertIn("Пассад", text)
        self.assertIn("🤖", text)
        self.assertNotIn("Тайная", text)                 # скрытые не показываем
        self.assertIn("<br/>", text)                     # Rich-разметка, не plain

    def test_write_screen_uses_shared_rich_constants(self):
        from presentation.menu_texts import WRITE_TEXT
        from presentation.texts import DM_COMMANDS_BLOCK, DM_HELP, FOOTER, HOW_TO_WRITE, REPLY_HINT
        self.assertEqual(WRITE_TEXT, DM_HELP)            # тот же текст, что у /help в ЛС
        for block in (HOW_TO_WRITE, REPLY_HINT, DM_COMMANDS_BLOCK, FOOTER):
            self.assertIn(block, WRITE_TEXT)

    def test_keyboards(self):
        from adapters.telegram.menu_keyboards import (
            city_kb, commune_kb, main_menu_kb, reply_menu_keyboard, when_kb)
        reply = reply_menu_keyboard()
        self.assertEqual(reply.keyboard[0][0].text, "(つ✧ω✧)つ")
        self.assertTrue(reply.is_persistent)
        rows = main_menu_kb().inline_keyboard
        self.assertEqual([[b.text for b in r] for r in rows],
                         [["Вписаться", "В гости"], ["Написать", "Список коммун"], ["Поддержка"]])
        self.assertEqual(rows[2][0].url, "https://t.me/thetrtk")
        self.assertEqual([b.text for b in city_kb().inline_keyboard[0]], ["Москва", "Питер", "Владик"])
        self.assertEqual([b.text for b in when_kb().inline_keyboard[0]],
                         ["Сегодня", "Завтра", "Послезавтра", "Назад"])
        kb = commune_kb([{"name": "A", "chat_id": -1001234567890123}]).inline_keyboard
        self.assertEqual([r[0].text for r in kb], ["A", "Назад"])      # столбец + Назад
        for row in kb:
            self.assertLess(len(row[0].callback_data.encode()), 64)


class FakeMessage:
    """Минимум aiogram.Message, нужный хендлерам меню."""
    counter = 1000

    def __init__(self, text=None, *, message_id=None, user_id=5, username="thetrtk"):
        FakeMessage.counter += 1
        self.message_id = message_id or FakeMessage.counter
        self.text, self.reply_to_message, self.message_thread_id = text, None, None
        self.chat = types.SimpleNamespace(id=user_id, type="private")
        self.from_user = types.SimpleNamespace(id=user_id, username=username, full_name="T")
        self.sent, self.edits, self.replies = [], [], []

    async def answer(self, text, reply_markup=None, **_):
        msg = FakeMessage(text)
        self.sent.append((text, reply_markup))
        return msg

    async def reply(self, text, **_):
        self.replies.append(text)

    async def edit_text(self, text=None, reply_markup=None, rich_message=None, **kw):
        self.edits.append((rich_message.html if rich_message else text, reply_markup))
        self.edit_kwargs = kw


class HandlerFlowTests(unittest.IsolatedAsyncioTestCase):
    """Настоящие хендлеры меню от кнопки до доставки и реакций."""

    def setUp(self):
        from adapters.telegram.handlers import menu
        self.menu = menu
        self.flows, self.chats = FlowRepo(), Chats()
        self.threads, self.channel, self.dm = ThreadRepo(), Channel(), Dm()
        repos = RepositoryBundle(self.chats, self.threads, Stats(), self.dm)

        class SF:
            async def draft(self, uid): return None
            async def clear_draft(self, uid): pass
        self.db = types.SimpleNamespace(
            get_menu_flow_repository=lambda: self.flows,
            get_repositories=lambda: repos,
            get_site_feedback_repository=lambda: SF(),
        )
        patches = [
            patch.object(menu, "db", self.db),
            patch.object(menu, "TelegramChannelAdapter", lambda bot: self.channel),
            patch.object(menu, "resolve_chat_and_text", self.no_commune),
            patch.object(menu, "send_rich", self.send_rich),
            patch.object(menu, "build_full_list_text", self.full_list),
        ]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        self.bot_calls = []

        async def edit_markup(**kw): self.bot_calls.append(kw)
        self.bot = types.SimpleNamespace(edit_message_reply_markup=edit_markup)

    @staticmethod
    async def no_commune(text, caption=None):
        return None

    async def send_rich(self, msg, html, topic_id=None, reply_markup=None):
        sent = FakeMessage(html)
        msg.sent.append((html, reply_markup))
        return sent

    async def full_list(self):
        repos = RepositoryBundle(self.chats, self.threads, Stats(), self.dm)
        return await AdminService(repos, 1, [("vlk", "Владивостокские", "⛵️")]).render_full_list_text()

    async def press(self, screen, action, arg=None):
        await self.menu.dispatch_callback(
            self.menu.flow_service(), screen, 5, action, arg)

    async def test_full_stay_flow(self):
        button = FakeMessage("(つ✧ω✧)つ")
        await self.menu.open_menu(button)
        self.assertEqual(button.sent[0][0], "<p>🪁Меню:</p>")

        screen = FakeMessage("🪁Меню:", message_id=50)
        await self.press(screen, "go", "stay")
        self.assertEqual(screen.edits[-1][0], "<p>Город?</p>")
        await self.press(screen, "city", "msk")
        self.assertEqual(screen.edits[-1][0], "<p>Коммуна?</p>")
        await self.press(screen, "c", "-1")
        await self.press(screen, "when", "today")
        self.assertEqual(screen.edits[-1][0], "<p>Напиши на сколько дней (напр. 4)</p>")

        await self.press(screen, "back")                           # назад к «Когда?»
        self.assertEqual(screen.edits[-1][0], "<p>Когда?</p>")
        await self.press(screen, "when", "today")

        bad = FakeMessage("четыре")
        await self.menu.on_flow_text(bad, self.bot)
        self.assertIn("число дней", bad.replies[0])

        days = FakeMessage("4")
        await self.menu.on_flow_text(days, self.bot)
        self.assertEqual(days.sent[0][0], "<p>Напиши прилагаемое послание</p>")
        self.assertEqual(self.bot_calls[-1]["message_id"], 50)     # старые кнопки убраны

        final = FakeMessage("привет, можно вписаться?", message_id=77)
        await self.menu.on_flow_text(final, self.bot)
        self.assertEqual(
            self.channel.calls[0][3],
            "<b>@thetrtk хочет вписаться сегодня на 4 дня</b>\n<b>↳ </b>привет, можно вписаться?")
        self.assertEqual([r[1] for r in self.channel.reactions], ["✍"])
        self.assertEqual(int(self.channel.reactions[-1][0].message_id), 77)
        self.assertIsNone(await self.flows.get(5))                  # сценарий закрыт
        self.assertEqual(final.sent[-1][0], "<p>🪁Меню:</p>")      # после отправки — снова меню
        self.assertIsNotNone(final.sent[-1][1])

    async def test_menu_not_shown_when_delivery_fails(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        await self.press(screen, "go", "guest")
        await self.press(screen, "city", "spb")
        await self.press(screen, "c", "-5")
        await self.press(screen, "when", "after")
        self.channel.fail_kind = "text"
        final = FakeMessage("можно?")
        await self.menu.on_flow_text(final, self.bot)
        self.assertEqual(final.sent, [])

    async def test_guest_flow_gets_pencil_reaction(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        await self.press(screen, "go", "guest")
        await self.press(screen, "city", "spb")
        await self.press(screen, "c", "-5")
        await self.press(screen, "when", "after")
        self.assertEqual(screen.edits[-1][0], "<p>Напиши прилагаемое послание</p>")
        await self.menu.on_flow_text(FakeMessage("можно?"), self.bot)
        self.assertIn("хочет прийти послезавтра в гости", self.channel.calls[0][3])
        self.assertEqual([r[1] for r in self.channel.reactions], ["✍"])

    async def test_stale_screen_and_back_to_menu(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        await self.press(screen, "go", "guest")
        old = FakeMessage("старый экран", message_id=49)
        with self.assertRaises(MenuFlowError):
            await self.press(old, "city", "msk")
        await self.press(screen, "back")                           # с «Город?» → меню
        self.assertEqual(screen.edits[-1][0], "<p>🪁Меню:</p>")
        with self.assertRaises(MenuFlowError):
            await self.press(screen, "city", "msk")                # сценарий уже закрыт

    async def test_edit_does_not_leak_default_parse_mode(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        await self.press(screen, "write")
        self.assertIsNone(screen.edit_kwargs["parse_mode"])        # не HTML по умолчанию бота
        self.assertIsNone(screen.edit_kwargs["link_preview_options"])

    async def test_info_screens(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        await self.press(screen, "write")
        from presentation.texts import DM_HELP
        self.assertEqual(screen.edits[-1][0], DM_HELP)             # Rich-константы Лакея
        await self.press(screen, "list")
        self.assertIn("<h2>к КоммуСети подключены:</h2>", screen.edits[-1][0])
        self.assertIn("Пассад", screen.edits[-1][0])

    async def test_normal_commune_message_mid_flow_is_not_swallowed(self):
        from aiogram.dispatcher.event.bases import SkipHandler
        screen = FakeMessage("🪁Меню:", message_id=50)
        for step in (("go", "guest"), ("city", "msk"), ("c", "-1"), ("when", "today")):
            await self.press(screen, *step)

        async def resolves(text, caption=None):
            return (self.chats.docs[-2], "привет")
        with patch.object(self.menu, "resolve_chat_and_text", resolves):
            with self.assertRaises(SkipHandler):
                await self.menu.on_flow_text(FakeMessage("Могильный Платон! привет"), self.bot)
        self.assertIsNone(await self.flows.get(5))
        self.assertFalse(self.channel.calls)

    async def test_failed_delivery_keeps_flow_for_retry(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        for step in (("go", "guest"), ("city", "msk"), ("c", "-1"), ("when", "today")):
            await self.press(screen, *step)
        self.channel.fail_kind = "text"
        msg = FakeMessage("можно?")
        await self.menu.on_flow_text(msg, self.bot)
        self.assertTrue(msg.replies)                                # «Не доставлено»
        self.assertEqual((await self.flows.get(5))["stage"], "text")

    async def test_command_resets_flow_except_anon(self):
        from aiogram.dispatcher.event.bases import SkipHandler
        screen = FakeMessage("🪁Меню:", message_id=50)
        await self.press(screen, "go", "guest")
        for text, kept in (("/anon", True), ("/deanon@lackey_bot", True), ("/list", False)):
            await self.press(screen, "go", "guest")
            with self.assertRaises(SkipHandler):
                await self.menu.reset_flow_on_command(FakeMessage(text))
            self.assertEqual(await self.flows.get(5) is not None, kept, text)


    # ── «Вписаться» → «В другой день» ───────────────────────────────────────
    async def test_stay_other_day_full_flow(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        for step in (("go", "stay"), ("city", "msk"), ("c", "-1")):
            await self.press(screen, *step)
        kb = screen.edits[-1][1].inline_keyboard                   # экран «Когда?»
        self.assertIn("В другой день", [b.text for r in kb for b in r])
        await self.press(screen, "when", "other")
        self.assertEqual(screen.edits[-1][0], "<p>Напиши, в какой день (напр. 15 ноября)</p>")

        await self.press(screen, "back")                           # назад к «Когда?»
        self.assertEqual(screen.edits[-1][0], "<p>Когда?</p>")
        await self.press(screen, "when", "other")

        date = FakeMessage("  15   ноября ")
        await self.menu.on_flow_text(date, self.bot)
        self.assertEqual(date.sent[0][0], "<p>Напиши на сколько дней (напр. 4)</p>")
        self.assertEqual(self.bot_calls[-1]["message_id"], 50)     # старые кнопки убраны
        self.assertEqual((await self.flows.get(5))["stage"], "days")

        await self.menu.on_flow_text(FakeMessage("4"), self.bot)
        final = FakeMessage("можно?", message_id=77)
        await self.menu.on_flow_text(final, self.bot)
        self.assertEqual(
            self.channel.calls[0][3],
            "<b>@thetrtk хочет вписаться 15 ноября на 4 дня</b>\n<b>↳ </b>можно?")
        self.assertEqual([r[1] for r in self.channel.reactions], ["✍"])
        self.assertIsNone(await self.flows.get(5))

    async def test_other_day_date_is_escaped_and_validated(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        for step in (("go", "stay"), ("city", "msk"), ("c", "-1"), ("when", "other")):
            await self.press(screen, *step)
        too_long = FakeMessage("x" * 41)
        await self.menu.on_flow_text(too_long, self.bot)
        self.assertIn("коротко", too_long.replies[0])
        self.assertEqual((await self.flows.get(5))["stage"], "date")   # остались на шаге
        await self.menu.on_flow_text(FakeMessage("<b>1 мая</b>"), self.bot)
        await self.menu.on_flow_text(FakeMessage("2"), self.bot)
        await self.menu.on_flow_text(FakeMessage("привет"), self.bot)
        self.assertIn("хочет вписаться &lt;b&gt;1 мая&lt;/b&gt; на 2 дня</b>", self.channel.calls[0][3])

    async def test_guest_has_no_other_day(self):
        screen = FakeMessage("🪁Меню:", message_id=50)
        for step in (("go", "guest"), ("city", "msk"), ("c", "-1")):
            await self.press(screen, *step)
        self.assertNotIn("В другой день", [b.text for r in screen.edits[-1][1].inline_keyboard for b in r])
        with self.assertRaises(MenuFlowError):
            await self.press(screen, "when", "other")              # руками подделанный callback

if __name__ == "__main__":
    unittest.main()
