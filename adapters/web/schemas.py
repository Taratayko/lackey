"""DTO для будущего Web/Site adapter.

Этот слой не зависит от FastAPI/Django/aiohttp. Он описывает JSON-форму,
которую будущий сайт коммун сможет отправлять в backend, а backend сможет
преобразовать в доменные `IncomingMessage` / `OutgoingMessage`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class WebAuthorPayload:
    """Автор web-сообщения в терминах сайта."""

    user_id: str
    username: str | None = None
    display_name: str | None = None
    session_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WebAuthorPayload":
        return cls(
            user_id=str(data.get("user_id") or data.get("id") or ""),
            username=data.get("username"),
            display_name=data.get("display_name") or data.get("name"),
            session_id=data.get("session_id"),
            meta=dict(data.get("meta") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "session_id": self.session_id,
            "meta": self.meta,
        }


@dataclass(frozen=True, slots=True)
class WebAttachmentPayload:
    """Вложение из web/API-запроса."""

    kind: str
    file_id: str | None = None
    url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WebAttachmentPayload":
        size = data.get("size")
        return cls(
            kind=str(data.get("kind") or data.get("type") or "unknown"),
            file_id=data.get("file_id") or data.get("external_file_id"),
            url=data.get("url"),
            filename=data.get("filename") or data.get("name"),
            mime_type=data.get("mime_type"),
            size=int(size) if size is not None and str(size).isdigit() else None,
            meta=dict(data.get("meta") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file_id": self.file_id,
            "url": self.url,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "meta": self.meta,
        }


@dataclass(frozen=True, slots=True)
class WebIncomingMessagePayload:
    """JSON-представление входящего сообщения с сайта.

    `community_id` — внешний id web-комнаты/коммуны на сайте. Позже он будет
    маппиться на Mongo chat/community record.
    """

    community_id: str
    message_id: str
    author: WebAuthorPayload
    text: str = ""
    attachments: tuple[WebAttachmentPayload, ...] = ()
    reply_to_message_id: str | None = None
    topic_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WebIncomingMessagePayload":
        community_id = data.get("community_id") or data.get("chat_id") or data.get("room_id")
        message_id = data.get("message_id") or data.get("id")
        if community_id is None:
            raise ValueError("web payload must contain community_id/chat_id/room_id")
        if message_id is None:
            raise ValueError("web payload must contain message_id/id")

        author_data = data.get("author") or {
            "user_id": data.get("user_id"),
            "username": data.get("username"),
            "display_name": data.get("display_name"),
            "session_id": data.get("session_id"),
        }
        if not author_data.get("user_id"):
            raise ValueError("web payload must contain author.user_id or user_id")

        attachments = tuple(
            WebAttachmentPayload.from_dict(item)
            for item in data.get("attachments", [])
            if isinstance(item, dict)
        )
        return cls(
            community_id=str(community_id),
            message_id=str(message_id),
            author=WebAuthorPayload.from_dict(author_data),
            text=str(data.get("text") or ""),
            attachments=attachments,
            reply_to_message_id=(
                str(data["reply_to_message_id"])
                if data.get("reply_to_message_id") is not None
                else None
            ),
            topic_id=str(data["topic_id"]) if data.get("topic_id") is not None else None,
            raw=dict(data),
            meta=dict(data.get("meta") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "community_id": self.community_id,
            "message_id": self.message_id,
            "author": self.author.to_dict(),
            "text": self.text,
            "attachments": [item.to_dict() for item in self.attachments],
            "reply_to_message_id": self.reply_to_message_id,
            "topic_id": self.topic_id,
            "meta": self.meta,
        }
