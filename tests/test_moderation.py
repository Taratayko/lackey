"""Moderation tests: ban/mute service, duration parsing, text triggers and /help.

Standard-library unittest only (aiogram is needed just for the handler-level checks).
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from application.services.moderation import (
    DEFAULT_MUTE, ModerationRequest, ModerationService, ModerationTarget,
    format_duration, parse_duration, parse_mute_args,
)
from ports.repositories import RepositoryBundle

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
CHAT = -1001
ADMIN, USER, BOT, OWNER = 10, 20, 99, 1


class FakeChannel:
    def __init__(self, restrictors=(ADMIN,), admins=(ADMIN,)):
        self.restrictors = set(restrictors)
        self.admins = set(admins)
        self.calls = []
        self.fail = None
        self.undeletable = set()

    async def can_restrict_members(self, target, user_id):
        return int(user_id) in self.restrictors

    async def is_chat_admin(self, target, user_id):
        return int(user_id) in self.admins

    async def get_self_user_id(self):
        return BOT

    async def ban_user(self, target, user_id):
        if self.fail:
            raise self.fail
        self.calls.append(("ban", int(user_id)))

    async def unban_user(self, target, user_id, only_if_banned=True):
        self.calls.append(("unban", int(user_id)))

    async def delete_message(self, message_ref):
        if int(message_ref.external_message_id) in self.undeletable:
            raise RuntimeError("message can't be deleted")
        self.calls.append(("delete", int(message_ref.external_message_id)))

    async def mute_user(self, target, user_id, until=None):
        if self.fail:
            raise self.fail
        self.calls.append(("mute", int(user_id), until))

    async def unmute_user(self, target, user_id):
        self.calls.append(("unmute", int(user_id)))


class FakeJournal:
    def __init__(self, fails=False):
        self.rows = {}
        self.fails = fails

    async def record(self, chat_id, user_id, kind, *, username, full_name,
                     actor_user_id, reason, until):
        if self.fails:
            raise RuntimeError("Mongo unavailable")
        self.rows[(chat_id, user_id, kind)] = {
            "chat_id": chat_id, "user_id": user_id, "kind": kind, "username": username,
            "full_name": full_name, "reason": reason, "until": until,
            "created_at": NOW,
        }

    async def remove(self, chat_id, user_id, kind):
        self.rows.pop((chat_id, user_id, kind), None)

    async def find_by_username(self, chat_id, username, kind):
        for row in self.rows.values():
            if (row["chat_id"], row["kind"]) == (chat_id, kind) and \
                    (row["username"] or "").lower() == username.lower():
                return row
        return None

    async def list_active(self, chat_id, kind):
        return [r for r in self.rows.values() if (r["chat_id"], r["kind"]) == (chat_id, kind)]


class FakeMessageLog:
    def __init__(self, ids=()):
        self.ids = {(CHAT, USER): list(ids)}

    async def save(self, chat_id, user_id, message_id):
        self.ids.setdefault((chat_id, user_id), []).append(message_id)

    async def pop_user_messages(self, chat_id, user_id):
        return self.ids.pop((chat_id, user_id), [])


def run(coro):
    return asyncio.run(coro)


def make_service(channel=None, journal=None, owner=OWNER, message_log=None):
    channel = channel or FakeChannel()
    journal = journal if journal is not None else FakeJournal()
    bundle = RepositoryBundle(None, None, None, None, moderation=journal, message_log=message_log)
    return ModerationService(bundle, channel, owner_user_id=owner, clock=lambda: NOW), channel, journal


def request(actor=ADMIN, target=USER, username="spammer", anonymous=False, reply_id=None):
    return ModerationRequest(
        CHAT, actor, ModerationTarget(target, username, "Spam Mer"), anonymous, reply_id,
    )


class DurationTests(unittest.TestCase):
    def test_parse_duration_units(self):
        self.assertEqual(parse_duration("30м"), timedelta(minutes=30))
        self.assertEqual(parse_duration("2ч"), timedelta(hours=2))
        self.assertEqual(parse_duration("1д"), timedelta(days=1))
        self.assertEqual(parse_duration("2н"), timedelta(weeks=2))
        self.assertEqual(parse_duration("15 min"), timedelta(minutes=15))
        self.assertEqual(parse_duration("3H"), timedelta(hours=3))
        for word, delta in (("30дней", timedelta(days=30)), ("1день", timedelta(days=1)),
                            ("30минут", timedelta(minutes=30)), ("2часа", timedelta(hours=2)),
                            ("1неделя", timedelta(weeks=1))):
            self.assertEqual(parse_duration(word), delta, word)

    def test_parse_duration_rejects_non_durations(self):
        for token in ("спам", "0м", "5", "ч", "10лет"):
            self.assertIsNone(parse_duration(token), token)

    def test_mute_args(self):
        self.assertEqual(parse_mute_args("").duration, DEFAULT_MUTE)
        args = parse_mute_args("2ч флуд в чате")
        self.assertEqual((args.duration, args.reason), (timedelta(hours=2), "флуд в чате"))
        # Первое слово — не срок: это уже причина, срок по умолчанию.
        args = parse_mute_args("флуд в чате")
        self.assertEqual((args.duration, args.reason), (DEFAULT_MUTE, "флуд в чате"))
        # Слова с пробелом: «30 дней», «1 день», «30 минут»
        for text, delta in (("30 дней", timedelta(days=30)), ("1 день", timedelta(days=1)),
                            ("30 минут", timedelta(minutes=30)), ("2 часа", timedelta(hours=2))):
            args = parse_mute_args(text)
            self.assertEqual((args.duration, args.reason), (delta, None), text)
        args = parse_mute_args("1 день флуд в чате")
        self.assertEqual((args.duration, args.reason), (timedelta(days=1), "флуд в чате"))
        self.assertIsNone(parse_mute_args("навсегда").duration)
        self.assertIsNone(parse_mute_args("навсегда спам").duration)
        self.assertEqual(parse_mute_args("навсегда спам").reason, "спам")
        # Дольше 366 дней Telegram всё равно считает бессрочным.
        self.assertIsNone(parse_mute_args("999д").duration)

    def test_format_duration(self):
        self.assertEqual(format_duration(timedelta(minutes=90)), "1 час 30 минут")
        self.assertEqual(format_duration(timedelta(days=30)), "30 дней")
        self.assertEqual(format_duration(timedelta(days=1)), "1 день")
        self.assertEqual(format_duration(timedelta(days=22, hours=2)), "22 дня 2 часа")
        self.assertEqual(format_duration(timedelta(minutes=11)), "11 минут")
        self.assertEqual(format_duration(timedelta(minutes=21)), "21 минута")
        self.assertEqual(format_duration(None), "навсегда")


class MuteTests(unittest.TestCase):
    def test_mute_sets_until_and_journals(self):
        service, channel, journal = make_service()
        result = run(service.mute(request(), timedelta(hours=2), "флуд"))
        self.assertTrue(result.success)
        self.assertEqual(channel.calls, [("mute", USER, NOW + timedelta(hours=2))])
        self.assertIn("@spammer", result.user_message)
        self.assertIn("2 часа", result.user_message)
        self.assertIn("флуд", result.user_message)
        self.assertIn((CHAT, USER, "mute"), journal.rows)

    def test_mute_forever_has_no_until(self):
        service, channel, _ = make_service()
        run(service.mute(request(), None))
        self.assertEqual(channel.calls, [("mute", USER, None)])

    def test_non_moderator_cannot_mute(self):
        service, channel, _ = make_service()
        result = run(service.mute(request(actor=USER + 1)))
        self.assertFalse(result.success)
        self.assertEqual(channel.calls, [])

    def test_cannot_mute_admin_self_or_bot(self):
        service, channel, _ = make_service(FakeChannel(restrictors=(ADMIN, 11), admins=(ADMIN, 11)))
        self.assertFalse(run(service.mute(request(target=11))).success)       # другой админ
        self.assertIn("Самомут", run(service.mute(request(target=ADMIN))).user_message)
        self.assertIn("invincible", run(service.mute(request(target=BOT))).user_message)
        self.assertEqual(channel.calls, [])

    def test_owner_and_anonymous_admin_are_allowed(self):
        service, channel, _ = make_service()
        self.assertTrue(run(service.mute(request(actor=OWNER))).success)
        self.assertTrue(run(service.mute(request(actor=None, anonymous=True))).success)
        self.assertEqual(len(channel.calls), 2)

    def test_telegram_error_is_friendly_and_not_journaled(self):
        channel = FakeChannel()
        channel.fail = Exception("Bad Request: not enough rights to restrict/unrestrict chat member")
        service, _, journal = make_service(channel)
        result = run(service.mute(request()))
        self.assertFalse(result.success)
        self.assertIn("нет права", result.user_message)
        self.assertEqual(journal.rows, {})

    def test_journal_failure_does_not_break_mute(self):
        service, channel, _ = make_service(journal=FakeJournal(fails=True))
        self.assertTrue(run(service.mute(request())).success)
        self.assertEqual(len(channel.calls), 1)

    def test_reason_is_html_escaped(self):
        service, _, _ = make_service()
        result = run(service.mute(request(), reason="<b>x</b>"))
        self.assertNotIn("<b>x", result.user_message)

    def test_unmute_by_username_and_by_id(self):
        service, channel, journal = make_service()
        run(service.mute(request()))
        result = run(service.unmute(CHAT, ADMIN, username="@Spammer"))
        self.assertTrue(result.success)
        self.assertEqual(channel.calls[-1], ("unmute", USER))
        self.assertNotIn((CHAT, USER, "mute"), journal.rows)
        self.assertTrue(run(service.unmute(CHAT, ADMIN, user_id=USER)).success)

    def test_unmute_unknown_username(self):
        service, channel, _ = make_service()
        result = run(service.unmute(CHAT, ADMIN, username="ghost"))
        self.assertFalse(result.success)
        self.assertEqual(channel.calls, [])


class BanTests(unittest.TestCase):
    def test_ban_journals_and_clears_mute(self):
        service, channel, journal = make_service()
        run(service.mute(request()))
        result = run(service.ban(request(), "реклама"))
        self.assertTrue(result.success)
        self.assertEqual(channel.calls[-1], ("ban", USER))
        self.assertIn((CHAT, USER, "ban"), journal.rows)
        self.assertNotIn((CHAT, USER, "mute"), journal.rows)

    def test_non_moderator_cannot_ban(self):
        service, channel, _ = make_service()
        self.assertFalse(run(service.ban(request(actor=USER + 1))).success)
        self.assertEqual(channel.calls, [])

    def test_unban_by_username_then_banlist_empty(self):
        service, channel, _ = make_service()
        run(service.ban(request()))
        self.assertIn("spammer", run(service.banlist_text(CHAT, ADMIN)).user_message)
        self.assertTrue(run(service.unban(CHAT, ADMIN, username="spammer")).success)
        self.assertEqual(channel.calls[-1], ("unban", USER))
        self.assertIn("пуст", run(service.banlist_text(CHAT, ADMIN)).user_message)

    def test_banlist_requires_rights(self):
        service, _, _ = make_service()
        self.assertFalse(run(service.banlist_text(CHAT, USER)).success)


class CleanupTests(unittest.TestCase):
    def test_ban_deletes_logged_messages_and_reply(self):
        log = FakeMessageLog([5, 6, 7])
        service, channel, _ = make_service(message_log=log)
        result = run(service.ban(request(reply_id=7)))  # 7 уже в журнале — без дубля
        self.assertTrue(result.success)
        deleted = sorted(c[1] for c in channel.calls if c[0] == "delete")
        self.assertEqual(deleted, [5, 6, 7])
        self.assertIn("Удалено сообщений: 3", result.user_message)
        self.assertEqual(log.ids, {})  # журнал очищен

    def test_reply_message_deleted_even_if_not_logged(self):
        service, channel, _ = make_service(message_log=FakeMessageLog())
        result = run(service.ban(request(reply_id=42)))
        self.assertIn(("delete", 42), channel.calls)
        self.assertIn("Удалено сообщений: 1", result.user_message)

    def test_delete_failures_are_reported_but_ban_stands(self):
        channel = FakeChannel()
        channel.undeletable = {6}
        service, _, _ = make_service(channel, message_log=FakeMessageLog([5, 6]))
        result = run(service.ban(request()))
        self.assertTrue(result.success)
        self.assertIn("Удалено сообщений: 1", result.user_message)
        self.assertIn("не удалось: 1", result.user_message)
        self.assertIn(("ban", USER), channel.calls)

    def test_mute_does_not_delete_messages(self):
        service, channel, _ = make_service(message_log=FakeMessageLog([5]))
        run(service.mute(request(reply_id=5)))
        self.assertFalse([c for c in channel.calls if c[0] == "delete"])

    def test_rejected_ban_deletes_nothing(self):
        service, channel, _ = make_service(message_log=FakeMessageLog([5]))
        run(service.ban(request(actor=USER + 1)))                      # нет прав
        run(service.ban(request(target=ADMIN, actor=OWNER)))           # цель — админ
        self.assertEqual(channel.calls, [])


class MutelistTests(unittest.TestCase):
    def test_empty(self):
        service, _, _ = make_service()
        self.assertIn("нет", run(service.mutelist_text(CHAT, ADMIN)).user_message)

    def test_lists_remaining_time_and_forever(self):
        service, _, _ = make_service()
        run(service.mute(request(), timedelta(days=2, hours=3), "флуд"))
        run(service.mute(request(target=21, username="other"), None))
        text = run(service.mutelist_text(CHAT, ADMIN)).user_message
        self.assertIn("@spammer — осталось 2 дня 3 часа — флуд", text)
        self.assertIn("@other — навсегда", text)

    def test_handles_naive_datetimes_from_mongo(self):
        service, _, journal = make_service()
        run(service.mute(request(), timedelta(hours=1)))
        row = journal.rows[(CHAT, USER, "mute")]
        row["until"] = row["until"].replace(tzinfo=None)  # так отдаёт motor
        self.assertIn("осталось 1 час", run(service.mutelist_text(CHAT, ADMIN)).user_message)

    def test_requires_rights(self):
        service, _, _ = make_service()
        self.assertFalse(run(service.mutelist_text(CHAT, USER)).success)


class RefusalTextTests(unittest.TestCase):
    """Что бот пишет, когда действие запрещено."""

    def test_refusal_messages(self):
        service, _, _ = make_service(FakeChannel(restrictors=(ADMIN,), admins=(ADMIN, 11)))
        self.assertEqual(run(service.ban(request(actor=USER + 1))).user_message,
                         "❌ Нужны права администратора на блокировку участников")
        self.assertEqual(run(service.ban(request(target=11))).user_message,
                         "❌ Нельзя применить это к администратору")
        self.assertEqual(run(service.ban(request(target=BOT))).user_message, "I am invincible 😈")
        self.assertEqual(run(service.ban(request(target=ADMIN))).user_message, "Самобан нельзя 🐈‍⬛")
        # обычный юзер против админа/бота: сначала отказ по правам
        self.assertEqual(run(service.ban(request(actor=USER + 1, target=11))).user_message,
                         "❌ Нужны права администратора на блокировку участников")
        self.assertEqual(run(service.ban(request(actor=USER + 1, target=BOT))).user_message,
                         "❌ Нужны права администратора на блокировку участников")


class HandlerTests(unittest.TestCase):
    def setUp(self):
        import os
        os.environ.setdefault("BOT_TOKEN", "123:abc")
        os.environ.setdefault("ADMIN_ID", "1")

    def test_text_triggers(self):
        from adapters.telegram.handlers.moderation import _text_action

        class Msg:
            def __init__(self, text):
                self.text = text

        self.assertEqual(_text_action(Msg("мут 2ч спам")), {"action": "mute", "args": "2ч спам"})
        self.assertEqual(_text_action(Msg("Бан")), {"action": "ban", "args": ""})
        self.assertEqual(_text_action(Msg("разбан @x"))["action"], "unban")
        self.assertEqual(_text_action(Msg("РАЗМУТ"))["action"], "unmute")
        self.assertFalse(_text_action(Msg("банка варенья")))
        # Формат ретрансляции «Название! текст» не перехватываем, даже если имя совпало с триггером.
        self.assertFalse(_text_action(Msg("Мут Лампа! привет")))
        self.assertFalse(_text_action(Msg(None)))

    def test_help_texts(self):
        from presentation.texts import GROUP_HELP, GROUP_HELP_ADMIN
        for cmd in ("/mute", "/unmute", "/mutelist", "/ban", "/unban", "/banlist"):
            self.assertIn(cmd, GROUP_HELP_ADMIN)
            self.assertNotIn(cmd, GROUP_HELP)
        self.assertNotIn("/megaban", GROUP_HELP_ADMIN)  # команды нет — в справке её быть не должно


if __name__ == "__main__":
    unittest.main()
