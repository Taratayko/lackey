"""Patch A regression suite. No Telegram token, DB or third-party packages needed."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import types
import unittest

from application.services.site_feedback import SiteFeedbackError, SiteFeedbackService
from domain.site_feedback import PAGE_MAP, PAGES, STATUS_NAMES, site_url, validate_slug


class MemoryRepo:
    def __init__(self):
        self.drafts = {}
        self.docs = {}
        self.users = {}
        self.counter = 0
        self.changes = []
        self.fail_changes = False
        self.extra_pages = {}

    async def draft(self, uid):
        return deepcopy(self.drafts.get(uid))

    async def save_draft(self, uid, draft):
        self.drafts[uid] = deepcopy(draft)

    async def clear_draft(self, uid):
        self.drafts.pop(uid, None)

    async def pages(self, include_admin=False):
        result = [await self.page(page.key) for page in PAGES
                  if include_admin or page.access == "public"]
        result.extend(self.extra_pages.values())
        return result

    async def page(self, key):
        if key in self.extra_pages:
            return self.extra_pages[key]
        page = PAGE_MAP.get(key)
        if not page:
            return None
        return {"_id": key, "route": page.route, "access": page.access, "title": page.title}

    async def create_item(self, doc):
        key = doc["submission_key"]
        if key not in self.docs:
            self.counter += 1
            self.docs[key] = {"_id": f"{self.counter:024x}", **deepcopy(doc)}
        return deepcopy(self.docs[key])

    async def get_item(self, item_id):
        return next((deepcopy(v) for v in self.docs.values() if str(v["_id"]) == item_id), None)

    async def items(self, selector, offset=0, limit=5):
        return deepcopy(self._match(selector)[offset:offset+limit])

    async def count(self, selector):
        return len(self._match(selector))

    def _match(self, selector):
        def match(item):
            for key, expected in selector.items():
                value = item
                for part in key.split("."):
                    value = value[part]
                if isinstance(expected, dict):
                    if value not in expected["$in"]:
                        return False
                elif value != expected:
                    return False
            return True
        return [item for item in self.docs.values() if match(item)]

    async def change_status(self, item, status, admin_id, note=""):
        if self.fail_changes:
            return False
        current = self.docs[item["submission_key"]]
        if current["status"] != item["status"]:
            return False
        current["status"] = status
        current["decision_by"] = admin_id
        current["decision_note"] = note
        current["history"].append({"action": "status", "from_status": item["status"],
                                   "to_status": status, "actor_user_id": admin_id, "comment": note})
        return True

    async def add_note(self, item, admin_id, note):
        current = self.docs[item["submission_key"]]
        current["decision_note"] = note
        current["history"].append({"action": "note", "comment": note})

    async def update_user(self, uid, username, full_name):
        saved = self.users.setdefault(uid, {"names": set()})
        saved["current"] = username
        saved["names"].add(username)

    async def user_ids_for_username(self, name):
        return [uid for uid, saved in self.users.items() if name.strip("@").lower() in saved["names"]]


class SiteFeedbackModelTests(unittest.TestCase):
    def test_real_page_catalog(self):
        self.assertGreaterEqual(len(PAGES), 8)
        self.assertEqual(PAGE_MAP["events"].route, "/events/")
        self.assertEqual(PAGE_MAP["commune_profile"].route, "/communes/{slug}/")
        self.assertEqual(PAGE_MAP["admin"].access, "admin_only")

    def test_url_building_does_not_invent_concrete_page(self):
        page = PAGE_MAP["commune_profile"]
        self.assertIsNone(site_url("https://example.org", page))
        self.assertEqual(site_url("https://example.org/", page, "klubnika"),
                         "https://example.org/communes/klubnika/")

    def test_slug_validity(self):
        self.assertEqual(validate_slug(" My-Commune "), "my-commune")
        for value in ("../admin", "https://bad", "<b>", "abc_def", "a"*65, "-bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_slug(value)


class SiteFeedbackFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo = MemoryRepo()
        self.service = SiteFeedbackService(self.repo, admin_id=999)
        self.user = types.SimpleNamespace(id=123, username="oldnick", full_name="Test <User>")

    async def review(self, page="events"):
        await self.service.begin(123, "page_feedback")
        await self.service.select_page(123, page)
        if "{slug}" in PAGE_MAP[page].route:
            await self.service.set_context(123, "klubnika")
        await self.service.set_feedback_type(123, "improvement")
        await self.service.accept_text(123, "Добавьте календарь мероприятий")
        return await self.service.submit(self.user, await self.repo.draft(123))

    async def test_regular_review_and_own_listing(self):
        saved = await self.review()
        self.assertEqual(saved["status"], "new")
        self.assertEqual(saved["page_key"], "events")
        items, n = await self.service.my_items(123)
        self.assertEqual(n, 1)
        self.assertEqual(items[0]["author"]["telegram_user_id"], 123)
        self.assertEqual(await self.repo.draft(123), None)

    async def test_new_page_and_history(self):
        await self.service.begin(123, "new_page")
        await self.service.accept_text(123, "Карта коммун")
        draft = await self.service.accept_text(123, "Добавить интерактивную карту расположения коммун")
        self.assertEqual(draft["stage"], "confirm")
        saved = await self.service.submit(self.user, draft)
        self.assertEqual(saved["kind"], "new_page")
        self.assertEqual(saved["title"], "Карта коммун")
        self.assertEqual(saved["history"][0]["action"], "created")

    async def test_future_manifest_page_without_python_patch(self):
        self.repo.extra_pages["site_map"] = {"_id": "site_map", "route": "/map/",
                                             "title": "Карта", "access": "public"}
        await self.service.begin(123, "page_feedback")
        draft = await self.service.select_page(123, "site_map")
        self.assertEqual(draft["stage"], "feedback_type")
        await self.service.set_feedback_type(123, "bug")
        draft = await self.service.accept_text(123, "Не открывается карта")
        item = await self.service.submit(self.user, draft)
        self.assertEqual(item["page_title"], "Карта")

    async def test_dynamic_page_slug(self):
        saved = await self.review("commune_profile")
        self.assertEqual(saved["context_slug"], "klubnika")

    async def test_private_admin_page_denied(self):
        await self.service.begin(123, "page_feedback")
        with self.assertRaisesRegex(SiteFeedbackError, "доступна"):
            await self.service.select_page(123, "admin")

    async def test_stage_guard_blocks_forged_callbacks(self):
        with self.assertRaises(SiteFeedbackError):
            await self.service.set_feedback_type(123, "improvement")
        await self.service.begin(123, "page_feedback")
        with self.assertRaises(SiteFeedbackError):
            await self.service.set_feedback_type(123, "improvement")

    async def test_malformed_lookups_not_authorized(self):
        item = await self.review()
        with self.assertRaises(SiteFeedbackError):
            await self.service.my_item(456, item["_id"])
        self.assertEqual((await self.service.my_items(456))[1], 0)

    async def test_reject_short_text(self):
        await self.service.begin(123, "new_page")
        with self.assertRaises(SiteFeedbackError):
            await self.service.accept_text(123, "ab")
        await self.service.accept_text(123, "Новый раздел")
        with self.assertRaises(SiteFeedbackError):
            await self.service.accept_text(123, "коротко")

    async def test_reject_excessive_text(self):
        await self.service.begin(123, "page_feedback")
        await self.service.select_page(123, "home")
        await self.service.set_feedback_type(123, "bug")
        with self.assertRaises(SiteFeedbackError):
            await self.service.accept_text(123, "a"*2001)

    async def test_idempotent_submission_with_same_uuid(self):
        await self.service.begin(123, "new_page")
        await self.service.accept_text(123, "Новый каталог")
        draft = await self.service.accept_text(123, "Создать раздел документации для новых участников")
        first = await self.service.submit(self.user, draft)
        second = await self.repo.create_item({"submission_key": draft["submission_key"], "body": "duplicate"})
        self.assertEqual(first["_id"], second["_id"])
        self.assertEqual(self.repo.counter, 1)

    async def test_double_submission_disallowed_after_commit(self):
        await self.service.begin(123, "new_page")
        await self.service.accept_text(123, "Новый каталог")
        draft = await self.service.accept_text(123, "Создать раздел документации для новых участников")
        await self.service.submit(self.user, draft)
        with self.assertRaises(SiteFeedbackError):
            await self.service.submit(self.user, draft)

    async def test_stale_draft_rejected(self):
        await self.service.begin(123, "new_page")
        await self.service.accept_text(123, "Новый каталог")
        draft = await self.service.accept_text(123, "Создать раздел документации для новых участников")
        wrong = dict(draft, submission_key="injected")
        with self.assertRaises(SiteFeedbackError):
            await self.service.submit(self.user, wrong)

    async def test_status_flow_and_history(self):
        item = await self.review()
        item = await self.service.change_status(999, item["_id"], "in_review")
        item = await self.service.change_status(999, item["_id"], "accepted")
        item = await self.service.change_status(999, item["_id"], "implemented")
        self.assertEqual(item["status"], "implemented")
        self.assertEqual(len(item["history"]), 4)

    async def test_admin_denied(self):
        item = await self.review()
        with self.assertRaises(SiteFeedbackError):
            await self.service.change_status(123, item["_id"], "accepted")
        with self.assertRaises(SiteFeedbackError):
            await self.service.admin_items(123, {})
        self.assertEqual((await self.repo.get_item(item["_id"]))["status"], "new")

    async def test_forbidden_transition(self):
        item = await self.review()
        with self.assertRaises(SiteFeedbackError):
            await self.service.change_status(999, item["_id"], "implemented")

    async def test_concurrent_status_change_conflict(self):
        item = await self.review()
        self.repo.fail_changes = True
        with self.assertRaises(SiteFeedbackError):
            await self.service.change_status(999, item["_id"], "accepted")
        self.assertEqual((await self.repo.get_item(item["_id"]))["status"], "new")

    async def test_admin_note(self):
        item = await self.review()
        changed = await self.service.add_note(999, item["_id"], "Сделаем в следующем релизе")
        self.assertEqual(changed["status"], "new")
        self.assertEqual(changed["history"][-1]["action"], "note")
        with self.assertRaises(SiteFeedbackError):
            await self.service.add_note(999, item["_id"], "x")

    async def test_username_mutates_not_owner_id(self):
        item = await self.review()
        await self.repo.update_user(123, "oldnick", "Test")
        await self.repo.update_user(123, "newnick", "Test")
        self.assertEqual(await self.repo.user_ids_for_username("@oldnick"), [123])
        self.assertEqual(await self.repo.user_ids_for_username("@newnick"), [123])
        self.assertEqual((await self.service.my_item(123, item["_id"]))["author"]["telegram_user_id"], 123)
        with self.assertRaises(SiteFeedbackError):
            await self.service.my_item(321, item["_id"])

    async def test_my_pagination(self):
        for index in range(7):
            await self.service.begin(123, "new_page")
            await self.service.accept_text(123, f"Страница {index}")
            draft = await self.service.accept_text(123, "Новый раздел с описанием возможных страниц")
            await self.service.submit(self.user, draft)
        a, total = await self.service.my_items(123, offset=0)
        b, _ = await self.service.my_items(123, offset=5)
        self.assertEqual(total, 7)
        self.assertEqual(len(a), 5)
        self.assertEqual(len(b), 2)
