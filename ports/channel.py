"""
Контракт канального адаптера.

Порт описывает возможности Telegram/VK/Web-адаптеров через доменные модели.
Runtime текущего бота пока остаётся на существующих aiogram-хэндлерах.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from domain.models import Attachment, ChannelInfo, ChannelRef, MessageRef, OutgoingMessage


@runtime_checkable
class ChannelPort(Protocol):
    """Минимальный будущий порт отправки/управления сообщениями."""

    async def send_message(self, message: OutgoingMessage) -> MessageRef:
        """Отправить универсальное сообщение и вернуть ссылку на созданное."""
        ...

    async def send_text(
        self,
        target: ChannelRef,
        text: str,
        reply_to: MessageRef | None = None,
        disable_web_page_preview: bool = False,
    ) -> MessageRef:
        """Быстрая отправка текста в канал."""
        ...

    async def send_album(
        self, target: ChannelRef, attachments: tuple[Attachment, ...],
        caption: str | None = None, reply_to: MessageRef | None = None,
    ) -> tuple[MessageRef, ...]:
        """Send multiple attachments, preserving every returned message reference."""
        ...

    async def forward_message(self, target: ChannelRef, source: MessageRef) -> MessageRef:
        """Forward an existing message to target (if supported by the channel)."""
        ...

    async def copy_message(self, target: ChannelRef, source: MessageRef) -> MessageRef:
        """Copy a message without Telegram forwarding attribution (privacy mode)."""
        ...

    async def send_photo_from_path(
        self,
        target: ChannelRef,
        photo_path: str | Path,
        caption: str | None = None,
    ) -> MessageRef:
        """Отправить локальную картинку/файл как фото, если канал это поддерживает."""
        ...

    async def delete_message(self, message_ref: MessageRef) -> None:
        """Удалить сообщение, если канал это поддерживает."""
        ...

    async def set_reaction(self, message_ref: MessageRef, emoji: str) -> None:
        """Поставить реакцию, если канал это поддерживает."""
        ...

    async def get_channel_info(self, target: ChannelRef) -> ChannelInfo:
        """Вернуть заголовок/ссылку внешнего канала, если доступно."""
        ...

    async def is_chat_admin(self, target: ChannelRef, user_id: str | int) -> bool:
        """Проверить, является ли пользователь администратором канала."""
        ...

    async def get_self_user_id(self) -> int:
        """Вернуть внешний id бота/адаптера в текущем канале."""
        ...

    async def ban_user(self, target: ChannelRef, user_id: str | int) -> None:
        """Забанить пользователя, если канал это поддерживает."""
        ...

    async def unban_user(
        self,
        target: ChannelRef,
        user_id: str | int,
        only_if_banned: bool = True,
    ) -> None:
        """Разбанить пользователя, если канал это поддерживает."""
        ...

    async def approve_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        """Одобрить заявку на вступление, если канал это поддерживает."""
        ...

    async def decline_join_request(self, target: ChannelRef, user_id: str | int) -> None:
        """Отклонить заявку на вступление, если канал это поддерживает."""
        ...
