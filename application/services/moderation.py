"""Сервис модерации.

Слой application для бана, мегабана, разбана, banlist и удаления
сохранённых сообщений пользователя. Сервис работает через repository layer и
ChannelPort, поэтому внутри нет прямых импортов aiogram или Mongo.
"""
from __future__ import annotations

from dataclasses import dataclass

from domain.models import ChannelRef, MessageRef
from ports.channel import ChannelPort
from ports.repositories import RepositoryBundle


@dataclass(frozen=True, slots=True)
class ModerationTarget:
    """Пользователь, над которым выполняется действие модерации."""

    user_id: int
    username: str | None = None

    @property
    def display_name(self) -> str:
        return f"@{self.username}" if self.username else f"id{self.user_id}"


@dataclass(frozen=True, slots=True)
class ModerationRequest:
    """Запрос на действие модерации в конкретном чате."""

    chat_id: int
    actor_user_id: int
    target: ModerationTarget


@dataclass(frozen=True, slots=True)
class ModerationResult:
    """Результат действия модерации для ответа пользователю."""

    success: bool
    user_message: str | None = None


class ModerationService:
    """Бизнес-логика банов без прямой зависимости от Telegram/Mongo."""

    def __init__(self, repositories: RepositoryBundle, channel: ChannelPort) -> None:
        self._repositories = repositories
        self._channel = channel

    async def is_chat_admin(self, chat_id: int, user_id: int) -> bool:
        """Проверить права администратора в чате."""
        return await self._channel.is_chat_admin(self._channel_ref(chat_id), user_id)

    async def ban(self, request: ModerationRequest) -> ModerationResult:
        """Забанить пользователя в текущем чате и удалить его сохранённые сообщения."""
        validation_error = await self._validate_action(request, permission_text="банить")
        if validation_error:
            return ModerationResult(success=False, user_message=validation_error)

        await self._repositories.bans.ban_user(
            request.chat_id,
            request.target.user_id,
            request.target.username,
            request.actor_user_id,
        )
        deleted = await self.cleanup_user_messages(request.chat_id, request.target.user_id)

        try:
            await self._channel.ban_user(
                self._channel_ref(request.chat_id),
                request.target.user_id,
            )
        except Exception as exc:
            return ModerationResult(
                success=False,
                user_message=f"❌ Не удалось выгнать: {exc}",
            )

        return ModerationResult(
            success=True,
            user_message=(
                f"🔨 {request.target.display_name} забанен | "
                f"удалено сообщений: {deleted}"
            ),
        )

    async def megaban(self, request: ModerationRequest) -> ModerationResult:
        """Забанить пользователя во всех зарегистрированных чатах."""
        validation_error = await self._validate_action(request, permission_text="банить")
        if validation_error:
            return ModerationResult(success=False, user_message=validation_error)

        success = 0
        failed = 0
        all_chats = await self._repositories.chats.get_all_chats()

        for chat in all_chats:
            chat_id = int(chat["chat_id"])
            await self.cleanup_user_messages(chat_id, request.target.user_id)
            try:
                await self._repositories.bans.ban_user(
                    chat_id,
                    request.target.user_id,
                    request.target.username,
                    request.actor_user_id,
                )
                await self._channel.ban_user(
                    self._channel_ref(chat_id),
                    request.target.user_id,
                )
                success += 1
            except Exception:
                failed += 1

        return ModerationResult(
            success=True,
            user_message=(
                f"🌐 {request.target.display_name} мегабан | "
                f"✅ {success} чатов | ❌ {failed} ошибок"
            ),
        )

    async def unban_by_username(
        self,
        chat_id: int,
        actor_user_id: int,
        username: str,
    ) -> ModerationResult:
        """Разбанить пользователя в текущем чате по username из banlist."""
        if not await self._channel.is_chat_admin(self._channel_ref(chat_id), actor_user_id):
            return ModerationResult(
                success=False,
                user_message="❌ Только администраторы могут разбанивать",
            )

        normalized_username = username.strip().lstrip("@")
        bans = await self._repositories.bans.get_bans(chat_id)
        target = next(
            (ban for ban in bans if ban.get("username") == normalized_username),
            None,
        )
        if not target:
            return ModerationResult(
                success=False,
                user_message=f"❌ @{normalized_username} не найден в чёрном списке",
            )

        await self._repositories.bans.unban_user(chat_id, int(target["user_id"]))
        try:
            await self._channel.unban_user(
                self._channel_ref(chat_id),
                int(target["user_id"]),
                only_if_banned=True,
            )
        except Exception:
            pass

        return ModerationResult(
            success=True,
            user_message=f"✅ @{normalized_username} разбанен",
        )

    async def banlist_text(self, chat_id: int, actor_user_id: int) -> ModerationResult:
        """Собрать текст banlist для текущего чата."""
        if not await self._channel.is_chat_admin(self._channel_ref(chat_id), actor_user_id):
            return ModerationResult(
                success=False,
                user_message="❌ Только администраторы могут смотреть чёрный список",
            )

        bans = await self._repositories.bans.get_bans(chat_id)
        if not bans:
            return ModerationResult(success=True, user_message="Чёрный список пуст 🕊")

        lines = ["<b>🔨 Чёрный список:</b>"]
        for ban in bans:
            name = f"@{ban['username']}" if ban.get("username") else f"id{ban['user_id']}"
            banned_at = ban.get("banned_at")
            date_str = banned_at.strftime("%d.%m.%Y") if banned_at else "?"
            lines.append(f"{name} — с {date_str}")

        return ModerationResult(success=True, user_message="\n".join(lines))

    async def cleanup_user_messages(self, chat_id: int, user_id: int) -> int:
        """Удалить сохранённые сообщения пользователя в чате и очистить индекс в БД."""
        saved_messages = await self._repositories.messages.get_user_messages(chat_id, user_id)
        deleted = 0
        for doc in saved_messages:
            try:
                await self._channel.delete_message(
                    MessageRef(
                        channel_ref=self._channel_ref(chat_id),
                        external_message_id=doc["message_id"],
                    )
                )
                deleted += 1
            except Exception:
                pass

        await self._repositories.messages.delete_user_messages_from_db(chat_id, user_id)
        return deleted

    async def save_user_message(self, chat_id: int, user_id: int, message_id: int) -> None:
        """Сохранить message_id для будущей зачистки при бане."""
        await self._repositories.messages.save_message(chat_id, user_id, message_id)

    async def _validate_action(
        self,
        request: ModerationRequest,
        *,
        permission_text: str,
    ) -> str | None:
        if not await self._channel.is_chat_admin(
            self._channel_ref(request.chat_id),
            request.actor_user_id,
        ):
            return f"❌ Только администраторы могут {permission_text}"

        if request.target.user_id == request.actor_user_id:
            return "Самобан нельзя 🐈‍⬛"

        self_user_id = await self._channel.get_self_user_id()
        if request.target.user_id == self_user_id:
            return "I am invincible 😈"

        return None

    @staticmethod
    def _channel_ref(chat_id: int) -> ChannelRef:
        return ChannelRef(channel="telegram", external_chat_id=chat_id)
