"""Web ChannelPort adapter MVP.

Патч 13 не запускает web-сервер и не добавляет FastAPI/aiohttp. Адаптер даёт
будущему сайту общий ChannelPort-контракт и простой outbox, куда application
core сможет складывать сообщения для web-интерфейса.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

from domain.models import ChannelInfo, ChannelKind, ChannelRef, MessageRef, OutgoingMessage
from ports.channel import ChannelPort

from .mapper import domain_message_to_web_dict


@runtime_checkable
class WebOutboxPort(Protocol):
    """Минимальный outbox для сообщений, которые должен увидеть сайт."""

    async def enqueue(self, message: OutgoingMessage) -> str:
        """Сохранить сообщение и вернуть web message id."""
        ...


class MemoryWebOutbox(WebOutboxPort):
    """In-memory outbox для smoke-тестов и будущих unit-тестов.

    Runtime сайта позже должен заменить его Mongo/Redis/WebSocket-реализацией.
    """

    def __init__(self) -> None:
        self._messages: list[dict] = []

    async def enqueue(self, message: OutgoingMessage) -> str:
        message_id = f"web-{uuid4().hex}"
        payload = domain_message_to_web_dict(message)
        payload["message_id"] = message_id
        self._messages.append(payload)
        return message_id

    def list_messages(self) -> list[dict]:
        return list(self._messages)


class WebChannelAdapter(ChannelPort):
    """Минимальная Web-реализация ChannelPort.

    Поддерживает текст и доменную отправку через outbox. Moderation/captcha
    capabilities для web-канала пока no-op/NotImplemented, потому что реальный
    сайт и его права появятся отдельным слоем.
    """

    def __init__(self, outbox: WebOutboxPort | None = None, public_base_url: str | None = None) -> None:
        self._outbox = outbox or MemoryWebOutbox()
        self._public_base_url = public_base_url

    async def send_message(self, message: OutgoingMessage) -> MessageRef:
        message_id = await self._outbox.enqueue(message)
        return MessageRef(
            channel_ref=ChannelRef(
                ChannelKind.WEB,
                message.ref.channel_ref.external_chat_id,
                topic_id=message.ref.channel_ref.topic_id,
                title=message.ref.channel_ref.title,
            ),
            external_message_id=message_id,
        )

    async def send_text(
        self,
        target: ChannelRef,
        text: str,
        reply_to: MessageRef | None = None,
        disable_web_page_preview: bool = False,
    ) -> MessageRef:
        message = OutgoingMessage(
            ref=MessageRef(channel_ref=target, external_message_id="pending"),
            text=text,
            reply_to=reply_to,
            meta={"disable_web_page_preview": disable_web_page_preview},
        )
        return await self.send_message(message)

    async def send_photo_from_path(
        self,
        target: ChannelRef,
        photo_path: str | Path,
        caption: str | None = None,
    ) -> MessageRef:
        raise NotImplementedError("Web file upload is not implemented in Patch 13 MVP")

    async def delete_message(self, message_ref: MessageRef) -> None:
        raise NotImplementedError("Web delete_message is not implemented in Patch 13 MVP")

    async def set_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        # Для web outbox delivery marker пока не нужен.
        return None

    async def get_channel_info(self, target: ChannelRef) -> ChannelInfo:
        link = None
        if self._public_base_url:
            link = f"{self._public_base_url.rstrip('/')}/communities/{target.external_chat_id}"
        return ChannelInfo(ref=target, title=target.title or f"Web {target.external_chat_id}", link=link)

    async def is_chat_admin(self, target: ChannelRef, user_id: str | int) -> bool:
        raise NotImplementedError("Web admin check is not implemented in Patch 13 MVP")

    async def get_self_user_id(self) -> int:
        raise NotImplementedError("Web adapter has no bot user id in Patch 13 MVP")

    async def ban_user(self, target: ChannelRef, user_id: str | int) -> None:
        raise NotImplementedError("Web moderation is not implemented in Patch 13 MVP")

    async def unban_user(
        self,
        target: ChannelRef,
        user_id: str | int,
        only_if_banned: bool = True,
    ) -> None:
        raise NotImplementedError("Web moderation is not implemented in Patch 13 MVP")

    async def approve_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        raise NotImplementedError("Web join requests are not implemented in Patch 13 MVP")

    async def decline_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        raise NotImplementedError("Web join requests are not implemented in Patch 13 MVP")
