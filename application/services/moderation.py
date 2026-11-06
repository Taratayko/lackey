"""Сервис модерации: бан, разбан, мут, размут и чёрный список чата.

Telegram остаётся источником истины: сами ограничения живут в нём, а журнал
(``repositories.moderation``) нужен, чтобы находить пользователя по @username
для /unban и /unmute и показывать /banlist. Сервис работает через ChannelPort
и RepositoryBundle, поэтому прямых импортов aiogram или Mongo здесь нет.
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from domain.models import ChannelRef, MessageRef
from ports.channel import ChannelPort
from ports.repositories import RepositoryBundle

logger = logging.getLogger(__name__)

DEFAULT_MUTE = timedelta(hours=1)
_DELETE_BATCH = 20   # удалений Telegram за один заход
_DELETE_PAUSE = 0.7  # пауза между заходами, сек
# Telegram считает ограничение дольше 366 суток (или короче 30 секунд) бессрочным.
MAX_MUTE = timedelta(days=366)

_FOREVER_WORDS = frozenset({"навсегда", "forever", "inf", "∞"})

_UNIT_SECONDS: dict[str, int] = {}
for _names, _seconds in (
    (("m", "min", "mins", "minute", "minutes",
      "м", "мин", "минута", "минуту", "минуты", "минут"), 60),
    (("h", "hr", "hrs", "hour", "hours",
      "ч", "час", "часа", "часов"), 3600),
    (("d", "day", "days",
      "д", "дн", "день", "дня", "дней"), 86400),
    (("w", "week", "weeks",
      "н", "нед", "неделя", "неделю", "недели", "недель"), 604800),
):
    for _name in _names:
        _UNIT_SECONDS[_name] = _seconds

_DURATION_RE = re.compile(r"^(\d{1,6})\s*([a-zа-яё]+)$", re.IGNORECASE)


def parse_duration(token: str) -> timedelta | None:
    """«30м», «2ч», «1д», «1н», «2h» → timedelta; не срок → None."""
    match = _DURATION_RE.match(token.strip())
    if not match:
        return None
    seconds = _UNIT_SECONDS.get(match.group(2).lower())
    amount = int(match.group(1))
    if seconds is None or amount <= 0:
        return None
    return timedelta(seconds=amount * seconds)


@dataclass(frozen=True, slots=True)
class MuteArgs:
    """Разобранные аргументы /mute: срок (None — навсегда) и причина."""

    duration: timedelta | None
    reason: str | None


def parse_mute_args(text: str | None) -> MuteArgs:
    """«2 часа спам», «2ч спам», «30 дней», «навсегда» → срок и причина.

    Срок можно писать слитно («2ч») или словами с пробелом («2 часа»).
    Без срока — час; если начало текста не срок, весь текст считается причиной.
    """
    text = (text or "").strip()
    words = text.split()
    if not words:
        return MuteArgs(DEFAULT_MUTE, None)

    if words[0].lower() in _FOREVER_WORDS:
        return MuteArgs(None, " ".join(words[1:]) or None)

    consumed = 1
    duration = parse_duration(words[0])
    if duration is None and words[0].isdigit() and len(words) > 1:
        duration = parse_duration(f"{words[0]} {words[1]}")
        consumed = 2
    if duration is None:
        return MuteArgs(DEFAULT_MUTE, text)

    if duration > MAX_MUTE:
        duration = None  # дольше 366 суток Telegram считает мут бессрочным
    return MuteArgs(duration, " ".join(words[consumed:]) or None)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_duration(delta: timedelta | None) -> str:
    """timedelta → «1 день 2 часа 30 минут»; None → «навсегда»."""
    if delta is None:
        return "навсегда"
    minutes = max(1, math.ceil(delta.total_seconds() / 60))
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} {_plural(days, 'день', 'дня', 'дней')}")
    if hours:
        parts.append(f"{hours} {_plural(hours, 'час', 'часа', 'часов')}")
    if minutes:
        parts.append(f"{minutes} {_plural(minutes, 'минута', 'минуты', 'минут')}")
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class ModerationTarget:
    """Пользователь, над которым выполняется действие модерации."""

    user_id: int
    username: str | None = None
    full_name: str | None = None

    @property
    def display_name(self) -> str:
        """Готовая к вставке в HTML подпись: @username, имя или id."""
        if self.username:
            return f"@{html.escape(self.username)}"
        if self.full_name:
            return html.escape(self.full_name)
        return f"id{self.user_id}"


@dataclass(frozen=True, slots=True)
class ModerationRequest:
    """Запрос на действие модерации в конкретном чате.

    ``actor_user_id`` равен None, если команду отправил анонимный админ
    (от имени самой группы): конкретный пользователь Telegram не раскрывается.
    """

    chat_id: int
    actor_user_id: int | None
    target: ModerationTarget
    anonymous_admin: bool = False
    reply_message_id: int | None = None  # сообщение, на которое ответила команда


@dataclass(frozen=True, slots=True)
class ModerationResult:
    """Результат действия модерации для ответа пользователю."""

    success: bool
    user_message: str | None = None


class ModerationService:
    """Бизнес-логика банов и мутов без прямой зависимости от Telegram/Mongo."""

    def __init__(
        self,
        repositories: RepositoryBundle,
        channel: ChannelPort,
        owner_user_id: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._journal = repositories.moderation
        self._messages = repositories.message_log
        self._channel = channel
        self._owner_user_id = owner_user_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ── Права ────────────────────────────────────────────────────────────────

    async def is_authorized(
        self, chat_id: int, actor_user_id: int | None, anonymous_admin: bool = False,
    ) -> bool:
        """Может ли отправитель банить и мутить в этом чате."""
        if anonymous_admin:
            return True  # анонимно писать от имени группы могут только админы
        if actor_user_id is None:
            return False
        if self._owner_user_id is not None and actor_user_id == self._owner_user_id:
            return True
        return await self._channel.can_restrict_members(self._channel_ref(chat_id), actor_user_id)

    # ── Бан ──────────────────────────────────────────────────────────────────

    async def ban(self, request: ModerationRequest, reason: str | None = None) -> ModerationResult:
        """Забанить пользователя в текущем чате."""
        error = await self._validate(request, self_text="Самобан нельзя 🐈‍⬛")
        if error:
            return ModerationResult(False, error)

        try:
            await self._channel.ban_user(self._channel_ref(request.chat_id), request.target.user_id)
        except Exception as exc:
            return ModerationResult(False, self._error_text(exc))

        await self._journal_record(request, "ban", reason, until=None)
        await self._journal_remove(request.chat_id, request.target.user_id, "mute")

        extra = (request.reply_message_id,) if request.reply_message_id else ()
        deleted, failed = await self.cleanup_user_messages(
            request.chat_id, request.target.user_id, extra_ids=extra,
        )
        text = (
            f"🔨 {request.target.display_name} забанен{self._reason_suffix(reason)}\n"
            f"🧹 Удалено сообщений: {deleted}"
        )
        if failed:
            text += f" (не удалось: {failed} — проверьте у бота право удалять сообщения)"
        return ModerationResult(True, text)

    async def unban(
        self,
        chat_id: int,
        actor_user_id: int | None,
        *,
        user_id: int | None = None,
        username: str | None = None,
        anonymous_admin: bool = False,
    ) -> ModerationResult:
        """Разбанить по id (reply/id) или по @username из журнала банов."""
        return await self._lift(
            "ban", chat_id, actor_user_id, user_id, username, anonymous_admin,
            not_found="не найден в чёрном списке", done="разбанен", icon="✅",
        )

    async def banlist_text(
        self, chat_id: int, actor_user_id: int | None, anonymous_admin: bool = False,
    ) -> ModerationResult:
        """Текст чёрного списка текущего чата."""
        if not await self.is_authorized(chat_id, actor_user_id, anonymous_admin):
            return ModerationResult(False, self._no_rights_text())
        if self._journal is None:
            return ModerationResult(False, "❌ Журнал банов недоступен")

        bans = await self._journal.list_active(chat_id, "ban")
        if not bans:
            return ModerationResult(True, "Чёрный список пуст 🕊")

        lines = ["<b>🔨 Чёрный список:</b>"]
        for ban in bans:
            name = ModerationTarget(
                int(ban["user_id"]), ban.get("username"), ban.get("full_name"),
            ).display_name
            created = ban.get("created_at")
            date_str = created.strftime("%d.%m.%Y") if created else "?"
            reason = f" — {html.escape(ban['reason'])}" if ban.get("reason") else ""
            lines.append(f"{name} — с {date_str}{reason}")
        return ModerationResult(True, "\n".join(lines))

    async def cleanup_user_messages(
        self, chat_id: int, user_id: int, extra_ids: tuple[int, ...] = (),
    ) -> tuple[int, int]:
        """Удалить сообщения участника, которые видел бот. Возвращает (удалено, ошибок)."""
        ids: list[int] = []
        if self._messages is not None:
            try:
                ids = await self._messages.pop_user_messages(chat_id, user_id)
            except Exception:
                logger.exception("Message log read failed (chat=%s)", chat_id)
        ids = list(dict.fromkeys([*ids, *extra_ids]))  # без дублей, порядок сохраняем

        ref = self._channel_ref(chat_id)
        deleted = failed = 0
        for start in range(0, len(ids), _DELETE_BATCH):
            if start:
                await asyncio.sleep(_DELETE_PAUSE)  # не упираемся в лимиты Bot API
            chunk = ids[start:start + _DELETE_BATCH]
            results = await asyncio.gather(
                *(self._channel.delete_message(MessageRef(channel_ref=ref, external_message_id=mid))
                  for mid in chunk),
                return_exceptions=True,
            )
            failed += sum(isinstance(r, Exception) for r in results)
            deleted += sum(not isinstance(r, Exception) for r in results)
        return deleted, failed

    # ── Мут ──────────────────────────────────────────────────────────────────

    async def mute(
        self,
        request: ModerationRequest,
        duration: timedelta | None = DEFAULT_MUTE,
        reason: str | None = None,
    ) -> ModerationResult:
        """Запретить пользователю писать на срок ``duration`` (None — навсегда)."""
        error = await self._validate(request, self_text="Самомут нельзя 🐈‍⬛")
        if error:
            return ModerationResult(False, error)

        until = self._clock() + duration if duration is not None else None
        try:
            await self._channel.mute_user(
                self._channel_ref(request.chat_id), request.target.user_id, until,
            )
        except Exception as exc:
            return ModerationResult(False, self._error_text(exc))

        await self._journal_record(request, "mute", reason, until=until)
        return ModerationResult(
            True,
            f"🔇 {request.target.display_name} в муте: {format_duration(duration)}"
            f"{self._reason_suffix(reason)}",
        )

    async def mutelist_text(
        self, chat_id: int, actor_user_id: int | None, anonymous_admin: bool = False,
    ) -> ModerationResult:
        """Текст списка замученных в текущем чате (с оставшимся временем)."""
        if not await self.is_authorized(chat_id, actor_user_id, anonymous_admin):
            return ModerationResult(False, self._no_rights_text())
        if self._journal is None:
            return ModerationResult(False, "❌ Журнал мутов недоступен")

        mutes = await self._journal.list_active(chat_id, "mute")
        if not mutes:
            return ModerationResult(True, "Замученных нет 🔊")

        now = self._clock()
        lines = ["<b>🔇 Замученные:</b>"]
        for mute in mutes:
            name = ModerationTarget(
                int(mute["user_id"]), mute.get("username"), mute.get("full_name"),
            ).display_name
            until = mute.get("until")
            if until is None:
                left = "навсегда"
            else:
                if until.tzinfo is None:  # Mongo отдаёт naive UTC
                    until = until.replace(tzinfo=timezone.utc)
                left = f"осталось {format_duration(until - now)}"
            reason = f" — {html.escape(mute['reason'])}" if mute.get("reason") else ""
            lines.append(f"{name} — {left}{reason}")
        return ModerationResult(True, "\n".join(lines))

    async def unmute(
        self,
        chat_id: int,
        actor_user_id: int | None,
        *,
        user_id: int | None = None,
        username: str | None = None,
        anonymous_admin: bool = False,
    ) -> ModerationResult:
        """Снять мут по id (reply/id) или по @username из журнала мутов."""
        return await self._lift(
            "mute", chat_id, actor_user_id, user_id, username, anonymous_admin,
            not_found="не найден среди замученных", done="снова может писать", icon="🔊",
        )

    # ── Внутреннее ───────────────────────────────────────────────────────────

    async def _lift(
        self,
        kind: str,
        chat_id: int,
        actor_user_id: int | None,
        user_id: int | None,
        username: str | None,
        anonymous_admin: bool,
        *,
        not_found: str,
        done: str,
        icon: str,
    ) -> ModerationResult:
        """Общая часть разбана и размута: найти цель, снять ограничение, стереть запись."""
        if not await self.is_authorized(chat_id, actor_user_id, anonymous_admin):
            return ModerationResult(False, self._no_rights_text())

        record = None
        if user_id is None:
            normalized = (username or "").strip().lstrip("@")
            if self._journal is not None and normalized:
                record = await self._journal.find_by_username(chat_id, normalized, kind)
            if not record:
                shown = html.escape(normalized) or "?"
                return ModerationResult(False, f"❌ @{shown} {not_found}")
            user_id = int(record["user_id"])
            username = record.get("username")

        target = ModerationTarget(user_id, (username or "").lstrip("@") or None,
                                  record.get("full_name") if record else None)
        ref = self._channel_ref(chat_id)
        try:
            if kind == "ban":
                await self._channel.unban_user(ref, user_id, only_if_banned=True)
            else:
                await self._channel.unmute_user(ref, user_id)
        except Exception as exc:
            return ModerationResult(False, self._error_text(exc))

        await self._journal_remove(chat_id, user_id, kind)
        return ModerationResult(True, f"{icon} {target.display_name} {done}")

    async def _validate(self, request: ModerationRequest, *, self_text: str) -> str | None:
        if not await self.is_authorized(
            request.chat_id, request.actor_user_id, request.anonymous_admin,
        ):
            return self._no_rights_text()

        if request.actor_user_id is not None and request.target.user_id == request.actor_user_id:
            return self_text

        if request.target.user_id == await self._channel.get_self_user_id():
            return "I am invincible 😈"

        if await self._channel.is_chat_admin(
            self._channel_ref(request.chat_id), request.target.user_id,
        ):
            return "❌ Нельзя применить это к администратору"
        return None

    async def _journal_record(
        self, request: ModerationRequest, kind: str, reason: str | None, until: datetime | None,
    ) -> None:
        """Журнал вторичен: сбой записи не должен превращать удавшийся бан в ошибку."""
        if self._journal is None:
            return
        try:
            await self._journal.record(
                request.chat_id,
                request.target.user_id,
                kind,
                username=request.target.username,
                full_name=request.target.full_name,
                actor_user_id=request.actor_user_id,
                reason=reason,
                until=until,
            )
        except Exception:
            logger.exception("Moderation journal write failed (%s, chat=%s)", kind, request.chat_id)

    async def _journal_remove(self, chat_id: int, user_id: int, kind: str) -> None:
        if self._journal is None:
            return
        try:
            await self._journal.remove(chat_id, user_id, kind)
        except Exception:
            logger.exception("Moderation journal cleanup failed (%s, chat=%s)", kind, chat_id)

    @staticmethod
    def _reason_suffix(reason: str | None) -> str:
        return f"\nПричина: {html.escape(reason)}" if reason else ""

    @staticmethod
    def _no_rights_text() -> str:
        return "❌ Нужны права администратора на блокировку участников"

    @staticmethod
    def _error_text(exc: Exception) -> str:
        low = str(exc).lower()
        if "not enough rights" in low or "chat_admin_required" in low:
            return "❌ У бота нет права блокировать участников — выдайте его в настройках админов чата"
        if "supergroup" in low:
            return "❌ Это действие работает только в супергруппах"
        if "participant_id_invalid" in low or "user not found" in low:
            return "❌ Пользователь не найден в этом чате"
        return f"❌ Не удалось выполнить: {html.escape(str(exc))}"

    @staticmethod
    def _channel_ref(chat_id: int) -> ChannelRef:
        return ChannelRef(channel="telegram", external_chat_id=chat_id)
