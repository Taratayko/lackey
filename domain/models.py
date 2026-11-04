"""Универсальные доменные модели communication core.

Модели не завязаны на aiogram, VK API, FastAPI или Mongo-драйвер.
Telegram/VK/Web-адаптеры должны приводить свои события к этим структурам,
а application-сервисы должны работать уже с ними.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class ChannelKind(str, Enum):
    """Поддерживаемые типы каналов."""

    TELEGRAM = "telegram"
    VK = "vk"
    WEB = "web"

    @classmethod
    def from_value(cls, value: "ChannelKind | str") -> "ChannelKind":
        if isinstance(value, cls):
            return value
        return cls(str(value).strip().lower())


class MessageDirection(str, Enum):
    """Направление сообщения относительно ядра."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"

    @classmethod
    def from_value(cls, value: "MessageDirection | str") -> "MessageDirection":
        if isinstance(value, cls):
            return value
        return cls(str(value).strip().lower())


class AttachmentKind(str, Enum):
    """Базовые типы вложений, общие для разных каналов."""

    PHOTO = "photo"
    VIDEO = "video"
    VIDEO_NOTE = "video_note"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    STICKER = "sticker"
    ANIMATION = "animation"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: "AttachmentKind | str") -> "AttachmentKind":
        if isinstance(value, cls):
            return value
        raw = str(value).strip().lower()
        return cls(raw) if raw in cls._value2member_map_ else cls.UNKNOWN


class AuthorKind(str, Enum):
    """Тип автора сообщения."""

    USER = "user"
    BOT = "bot"
    SYSTEM = "system"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: "AuthorKind | str") -> "AuthorKind":
        if isinstance(value, cls):
            return value
        raw = str(value).strip().lower()
        return cls(raw) if raw in cls._value2member_map_ else cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class ChannelRef:
    """Ссылка на чат/диалог во внешнем канале."""

    channel: ChannelKind | str
    external_chat_id: str | int
    topic_id: str | int | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", ChannelKind.from_value(self.channel))
        object.__setattr__(self, "external_chat_id", str(self.external_chat_id))
        if self.topic_id is not None:
            object.__setattr__(self, "topic_id", str(self.topic_id))

    @property
    def chat_id(self) -> str:
        """Короткий alias для переходного слоя."""
        return self.external_chat_id

    @property
    def key(self) -> str:
        """Стабильный ключ для логов/маппинга."""
        if self.topic_id:
            return f"{self.channel.value}:{self.external_chat_id}:{self.topic_id}"
        return f"{self.channel.value}:{self.external_chat_id}"


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    """Короткое описание внешнего канала для application-сервисов."""

    ref: ChannelRef
    title: str | None = None
    link: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Ссылка на конкретное сообщение во внешнем канале."""

    channel_ref: ChannelRef
    external_message_id: str | int

    def __post_init__(self) -> None:
        object.__setattr__(self, "external_message_id", str(self.external_message_id))

    @property
    def channel(self) -> str:
        return self.channel_ref.channel.value

    @property
    def chat_id(self) -> str:
        return self.channel_ref.external_chat_id

    @property
    def topic_id(self) -> str | None:
        return self.channel_ref.topic_id

    @property
    def message_id(self) -> str:
        return self.external_message_id


@dataclass(frozen=True, slots=True)
class MessageAuthor:
    """Автор сообщения без привязки к конкретному SDK."""

    external_user_id: str | int | None
    username: str | None = None
    display_name: str | None = None
    kind: AuthorKind | str = AuthorKind.USER
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.external_user_id is not None:
            object.__setattr__(self, "external_user_id", str(self.external_user_id))
        object.__setattr__(self, "kind", AuthorKind.from_value(self.kind))

    @property
    def user_id(self) -> str | None:
        """Короткий alias для переходного слоя."""
        return self.external_user_id


@dataclass(frozen=True, slots=True)
class Attachment:
    """Вложение сообщения с внешним file_id/url и необязательными метаданными."""

    kind: AttachmentKind | str
    external_file_id: str | None = None
    url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", AttachmentKind.from_value(self.kind))
        if self.external_file_id is not None:
            object.__setattr__(self, "external_file_id", str(self.external_file_id))


@dataclass(frozen=True, slots=True)
class Message:
    """Универсальное сообщение для application-слоя."""

    ref: MessageRef
    author: MessageAuthor | None = None
    text: str | None = None
    attachments: Iterable[Attachment] = field(default_factory=tuple)
    reply_to: MessageRef | None = None
    direction: MessageDirection | str = MessageDirection.INCOMING
    raw: Any | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachments", tuple(self.attachments))
        object.__setattr__(self, "direction", MessageDirection.from_value(self.direction))

    @classmethod
    def from_legacy(
        cls,
        channel: ChannelKind | str,
        chat_id: str | int,
        message_id: str | int,
        sender_id: str | int | None = None,
        text: str | None = None,
        attachments: Iterable[Attachment] = (),
        reply_to_message_id: str | int | None = None,
        topic_id: str | int | None = None,
        raw: Any | None = None,
    ) -> "Message":
        channel_ref = ChannelRef(channel=channel, external_chat_id=chat_id, topic_id=topic_id)
        reply_to = None
        if reply_to_message_id is not None:
            reply_to = MessageRef(channel_ref=channel_ref, external_message_id=reply_to_message_id)
        author = MessageAuthor(external_user_id=sender_id) if sender_id is not None else None
        return cls(
            ref=MessageRef(channel_ref=channel_ref, external_message_id=message_id),
            author=author,
            text=text,
            attachments=attachments,
            reply_to=reply_to,
            raw=raw,
        )

    @property
    def channel(self) -> str:
        return self.ref.channel

    @property
    def chat_id(self) -> str:
        return self.ref.chat_id

    @property
    def message_id(self) -> str:
        return self.ref.message_id

    @property
    def sender_id(self) -> str | None:
        return self.author.user_id if self.author else None

    @property
    def reply_to_message_id(self) -> str | None:
        return self.reply_to.message_id if self.reply_to else None


@dataclass(frozen=True, slots=True)
class IncomingMessage(Message):
    """Сообщение, пришедшее из Telegram/VK/Web в ядро."""

    direction: MessageDirection | str = MessageDirection.INCOMING


@dataclass(frozen=True, slots=True)
class OutgoingMessage(Message):
    """Сообщение, которое ядро просит отправить во внешний канал."""

    direction: MessageDirection | str = MessageDirection.OUTGOING


MessageAttachment = Attachment
