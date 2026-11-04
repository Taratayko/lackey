"""Business rules for Communars site feedback; no Telegram SDK dependencies."""
from __future__ import annotations

from uuid import uuid4

from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)
from domain.site_feedback import FEEDBACK_TYPES, PAGE_KEY_PATTERN, STATUS_NEXT, validate_slug


class SiteFeedbackError(ValueError):
    pass


class SiteFeedbackService:
    def __init__(self, repo, admin_id: int):
        self.repo = repo
        self.admin_id = admin_id

    async def begin(self, user_id: int, kind: str):
        if kind not in {"page_feedback", "new_page"}:
            raise SiteFeedbackError("Неизвестный тип обращения.")
        draft = {
            "kind": kind, "stage": "page" if kind == "page_feedback" else "new_title",
            "submission_key": uuid4().hex,
        }
        await self.repo.save_draft(user_id, draft)
        return draft

    async def select_page(self, user_id: int, page_key: str):
        draft = await self._draft(user_id, "page")
        doc = await self.repo.page(page_key)
        if not PAGE_KEY_PATTERN.fullmatch(page_key) or not doc or doc.get("access") != "public":
            raise SiteFeedbackError("Эта страница не доступна для оценки.")
        draft["page_key"] = page_key
        draft["stage"] = "context" if "{slug}" in doc["route"] else "feedback_type"
        await self.repo.save_draft(user_id, draft)
        return draft

    async def set_context(self, user_id: int, raw_slug: str | None):
        draft = await self._draft(user_id, "context")
        draft["context_slug"] = validate_slug(raw_slug) if raw_slug else None
        draft["stage"] = "feedback_type"
        await self.repo.save_draft(user_id, draft)
        return draft

    async def set_feedback_type(self, user_id: int, feedback_type: str):
        draft = await self._draft(user_id, "feedback_type")
        if feedback_type not in FEEDBACK_TYPES:
            raise SiteFeedbackError("Неизвестный тип отзыва.")
        draft["feedback_type"] = feedback_type
        draft["stage"] = "body"
        await self.repo.save_draft(user_id, draft)
        return draft

    async def accept_text(self, user_id: int, raw_text: str):
        draft = await self.repo.draft(user_id)
        if not draft or draft.get("stage") not in {"body", "new_title", "new_body"}:
            raise SiteFeedbackError("Нет активного шага ввода текста.")
        text = raw_text.strip()
        stage = draft["stage"]
        minimum, maximum = (4, 100) if stage == "new_title" else (10, 2000)
        if stage == "new_body":
            minimum = 20
        if not minimum <= len(text) <= maximum:
            raise SiteFeedbackError(f"Текст: от {minimum} до {maximum} символов.")
        if stage == "new_title":
            draft["title"] = text
            draft["stage"] = "new_body"
        else:
            draft["body"] = text
            draft["stage"] = "confirm"
        await self.repo.save_draft(user_id, draft)
        return draft

    async def submit(self, user, draft: dict):
        user_id = user.id
        current = await self._draft(user_id, "confirm")
        if draft.get("submission_key") != current["submission_key"]:
            raise SiteFeedbackError("Черновик изменился. Откройте новое меню.")
        kind = current["kind"]
        selected = await self.repo.page(current["page_key"]) if kind == "page_feedback" else None
        if kind == "page_feedback" and (not selected or selected.get("access") != "public"):
            raise SiteFeedbackError("Страница больше не доступна для отзывов.")
        doc = {
            "kind": kind, "page_key": current.get("page_key"),
            "page_title": selected["title"] if selected else None,
            "context_slug": current.get("context_slug"),
            "feedback_type": current.get("feedback_type"),
            "title": current.get("title", ""), "body": current["body"],
            "author": {
                "telegram_user_id": user_id,
                "username_snapshot": user.username or "",
                "display_name_snapshot": user.full_name or "",
            },
            "visibility": "moderators_only", "status": "new",
            "submission_key": current["submission_key"],
            "created_at": utcnow(), "updated_at": utcnow(),
            "history": [{"action": "created", "actor_user_id": user_id,
                         "from_status": None, "to_status": "new", "comment": "",
                         "created_at": utcnow()}],
        }
        item = await self.repo.create_item(doc)
        if not item:
            raise RuntimeError("MongoDB не подтвердила сохранение обращения.")
        await self.repo.clear_draft(user_id)
        return item

    async def _draft(self, user_id: int, stage: str):
        draft = await self.repo.draft(user_id)
        if not draft or draft.get("stage") != stage:
            raise SiteFeedbackError("Этот шаг устарел. Вызовите /communesite ещё раз.")
        return draft

    async def my_items(self, user_id: int, offset: int = 0, limit: int = 5):
        selector = {"author.telegram_user_id": user_id}
        return await self.repo.items(selector, offset, limit), await self.repo.count(selector)

    async def my_item(self, user_id: int, item_id: str):
        item = await self.repo.get_item(item_id)
        if not item or item["author"]["telegram_user_id"] != user_id:
            raise SiteFeedbackError("Обращение не найдено.")
        return item

    def assert_owner(self, user_id: int):
        if user_id != self.admin_id:
            raise SiteFeedbackError("Недостаточно прав.")

    async def change_status(self, actor: int, item_id: str, status: str):
        self.assert_owner(actor)
        item = await self.repo.get_item(item_id)
        if not item:
            raise SiteFeedbackError("Обращение не найдено.")
        if status not in STATUS_NEXT[item["status"]]:
            raise SiteFeedbackError("Этот переход статуса запрещён или уже выполнен.")
        if not await self.repo.change_status(item, status, actor):
            raise SiteFeedbackError("Статус уже изменился, обновите карточку.")
        return await self.repo.get_item(item_id)

    async def add_note(self, actor: int, item_id: str, note: str):
        self.assert_owner(actor)
        if not 3 <= len(note.strip()) <= 1000:
            raise SiteFeedbackError("Комментарий: от 3 до 1000 символов.")
        item = await self.repo.get_item(item_id)
        if not item:
            raise SiteFeedbackError("Обращение не найдено.")
        await self.repo.add_note(item, actor, note.strip())
        return await self.repo.get_item(item_id)

    async def admin_items(self, actor: int, selector: dict, offset: int = 0):
        self.assert_owner(actor)
        return await self.repo.items(selector, offset, 5), await self.repo.count(selector)
