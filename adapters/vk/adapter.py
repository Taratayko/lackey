"""VK ChannelPort adapter MVP.

Патч 12 не подключает VK к `bot.py` и не запускает Long Poll. Этот адаптер —
первый изолированный слой для будущего runtime: application service сможет
отправлять текст через ChannelPort, не зная о VK API.
"""
from __future__ import annotations

from pathlib import Path

from domain.models import ChannelInfo, ChannelKind, ChannelRef, MessageRef, OutgoingMessage
from ports.channel import ChannelPort

from .client import VkApiClient


class VkChannelAdapter(ChannelPort):
    """Минимальная VK-реализация ChannelPort.

    MVP поддерживает отправку обычного текста. Медиа, moderation и join-request
    capabilities будут добавляться позже, когда появится реальный VK runtime.
    """

    def __init__(self, client: VkApiClient) -> None:
        self._client = client

    async def send_message(self, message: OutgoingMessage) -> MessageRef:
        if message.attachments:
            raise NotImplementedError("VK media sending is not implemented in Patch 12 MVP")
        return await self.send_text(message.ref.channel_ref, message.text or "", message.reply_to)

    async def send_text(
        self,
        target: ChannelRef,
        text: str,
        reply_to: MessageRef | None = None,
        disable_web_page_preview: bool = False,
    ) -> MessageRef:
        # reply_to и disable_web_page_preview оставлены в сигнатуре порта.
        # Реальное VK reply/keyboard поведение будет добавлено отдельным патчем.
        vk_message_id = await self._client.messages_send(
            peer_id=target.external_chat_id,
            message=text,
        )
        return MessageRef(
            channel_ref=ChannelRef(ChannelKind.VK, target.external_chat_id, title=target.title),
            external_message_id=vk_message_id,
        )

    async def send_photo_from_path(
        self,
        target: ChannelRef,
        photo_path: str | Path,
        caption: str | None = None,
    ) -> MessageRef:
        raise NotImplementedError("VK photo upload is not implemented in Patch 12 MVP")

    async def delete_message(self, message_ref: MessageRef) -> None:
        raise NotImplementedError("VK delete_message is not implemented in Patch 12 MVP")

    async def set_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        # В VK MVP реакций как delivery marker нет. Оставляем no-op, чтобы сервисы
        # могли использовать ChannelPort без падения на необязательной операции.
        return None

    async def get_channel_info(self, target: ChannelRef) -> ChannelInfo:
        return ChannelInfo(ref=target, title=target.title or f"VK {target.external_chat_id}")

    async def is_chat_admin(self, target: ChannelRef, user_id: str | int) -> bool:
        raise NotImplementedError("VK admin check is not implemented in Patch 12 MVP")

    async def get_self_user_id(self) -> int:
        raise NotImplementedError("VK self user id is not implemented in Patch 12 MVP")

    async def ban_user(self, target: ChannelRef, user_id: str | int) -> None:
        raise NotImplementedError("VK ban_user is not implemented in Patch 12 MVP")

    async def unban_user(
        self,
        target: ChannelRef,
        user_id: str | int,
        only_if_banned: bool = True,
    ) -> None:
        raise NotImplementedError("VK unban_user is not implemented in Patch 12 MVP")

    async def approve_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        raise NotImplementedError("VK join requests are not supported in Patch 12 MVP")

    async def decline_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        raise NotImplementedError("VK join requests are not supported in Patch 12 MVP")
