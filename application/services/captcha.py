"""CaptchaService для онбординга новых участников.

Сервис держит логику chat_join_request, captcha session, ответа пользователя,
approve/decline и онбординг-лога. Внутри нет aiogram и прямого доступа к
Mongo-драйверу: Telegram-specific действия идут через ChannelPort, хранение —
через repository layer.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape as _html_escape
from pathlib import Path

from domain.models import ChannelInfo, ChannelRef
from ports.channel import ChannelPort
from ports.repositories import RepositoryBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptchaSettings:
    """Настройки онбординга, переданные из config/.env переходным слоем."""

    timeout_hours: int
    timeout_minutes: int
    approve_delay_seconds: int
    onboarding_text: str
    hello_image_path: Path


@dataclass(frozen=True, slots=True)
class CaptchaJoinRequest:
    """Заявка пользователя на вступление во внешний канал."""

    chat_id: int
    chat_title: str
    user_id: int
    username: str | None = None


@dataclass(frozen=True, slots=True)
class CaptchaAnswerRequest:
    """Ответ пользователя на вопрос онбординга."""

    user_id: int
    username: str | None
    text: str


@dataclass(frozen=True, slots=True)
class CaptchaCommandResult:
    """Результат captcha-команды для ответа пользователю."""

    handled: bool
    user_message: str | None = None


class CaptchaService:
    """Бизнес-логика онбординга без прямой зависимости от aiogram/Mongo."""

    def __init__(
        self,
        repositories: RepositoryBundle,
        settings: CaptchaSettings,
        channel: ChannelPort | None = None,
    ) -> None:
        self._repositories = repositories
        self._settings = settings
        self._channel = channel

    async def user_has_pending_captcha(self, user_id: int) -> bool:
        """Проверить, есть ли у пользователя активная pending-сессия."""
        session = await self._repositories.captcha.get_pending_session(user_id)
        return self._is_session_active(session)

    async def should_handle_private_answer(self, user_id: int, text: str | None) -> bool:
        """Фильтр для ЛС: перехватывать только не-командные ответы по активной сессии."""
        if text and text.startswith("/"):
            return False
        return await self.user_has_pending_captcha(user_id)

    async def handle_join_request(self, request: CaptchaJoinRequest) -> bool:
        """Начать онбординг по заявке на вступление.

        Возвращает True, если чат включён в капчу и заявка обработана сервисом.
        False значит, что чат не captcha-enabled и событие можно игнорировать.
        """
        if not await self._repositories.captcha.is_captcha_chat(request.chat_id):
            return False

        channel = self._require_channel()
        greeting = (
            f"Привет🖖\n"
            f"Я вижу, ты подал(а) заявку на вступление в "
            f"<b>{_escape_html(request.chat_title)}</b>.\n\n"
            f"Расскажи немного о себе — кто ты и что ищешь?\n\n"
            f"У тебя есть <b>{self.timeout_str()}</b> чтобы ответить❕"
        )

        try:
            sent_ref = await channel.send_text(
                target=self._private_channel(request.user_id),
                text=greeting,
            )
        except Exception as exc:
            logger.warning("Не удалось написать пользователю %d: %s", request.user_id, exc)
            return True

        await self._repositories.captcha.create_session(
            user_id=request.user_id,
            chat_id=request.chat_id,
            message_id=int(sent_ref.message_id),
            expires_at=self.expires_at(),
            username=request.username,
        )

        asyncio.create_task(
            self.expire_after(request.user_id, request.chat_id, self.timeout().total_seconds())
        )
        logger.info("Онбординг начат: user=%d chat=%d", request.user_id, request.chat_id)
        return True

    async def handle_onboarding_answer(self, request: CaptchaAnswerRequest) -> CaptchaCommandResult:
        """Сохранить анкету, отправить intro и запланировать approve."""
        session = await self._repositories.captcha.get_pending_session(request.user_id)
        if not self._is_session_active(session):
            return CaptchaCommandResult(handled=False)

        chat_id = int(session["chat_id"])
        await self._repositories.captcha.save_onboarding_answer(request.user_id, request.text)

        channel = self._require_channel()
        try:
            if self._settings.hello_image_path.exists():
                await channel.send_photo_from_path(
                    target=self._private_channel(request.user_id),
                    photo_path=self._settings.hello_image_path,
                    caption=self._settings.onboarding_text,
                )
            else:
                logger.warning("hello.png не найден по пути %s", self._settings.hello_image_path)
                await channel.send_text(
                    target=self._private_channel(request.user_id),
                    text=self._settings.onboarding_text,
                    disable_web_page_preview=True,
                )
        except Exception as exc:
            logger.warning(
                "Не удалось отправить онбординг-сообщение user=%d: %s",
                request.user_id,
                exc,
            )

        asyncio.create_task(
            self.approve_after_delay(request.user_id, chat_id, request.username)
        )
        logger.info("Онбординг: ответ получен user=%d chat=%d", request.user_id, chat_id)
        return CaptchaCommandResult(handled=True)

    async def approve_after_delay(self, user_id: int, chat_id: int, username: str | None) -> None:
        """Подождать approve_delay_seconds и одобрить заявку, если сессия ещё pending."""
        await asyncio.sleep(self._settings.approve_delay_seconds)

        session = await self._repositories.captcha.get_pending_session(user_id)
        if not session or int(session.get("chat_id")) != chat_id:
            return

        await self._repositories.captcha.resolve_pending_sessions(user_id, "solved")

        channel = self._require_channel()
        try:
            await channel.approve_join_request(self._chat_channel(chat_id), user_id)
            chat_info = await channel.get_channel_info(self._chat_channel(chat_id))
            chat_title = chat_info.title or str(chat_id)

            await channel.send_text(
                target=self._private_channel(user_id),
                text=f"Добро пожаловать в <b>{_escape_html(chat_title)}</b> 🎉",
            )

            asyncio.create_task(
                self._send_onboarding_log(
                    user_id=user_id,
                    username=username,
                    joined_chat_id=chat_id,
                    joined_chat_title=chat_title,
                    joined_chat_link=chat_info.link,
                    onboarding_answer=session.get("onboarding_answer"),
                )
            )
        except Exception as exc:
            logger.warning("Не удалось одобрить заявку user=%d chat=%d: %s", user_id, chat_id, exc)

    async def expire_after(self, user_id: int, chat_id: int, delay: float) -> None:
        """Отклонить заявку после таймаута, если пользователь не ответил."""
        await asyncio.sleep(delay)

        session = await self._repositories.captcha.get_pending_session(user_id)
        if not session or int(session.get("chat_id")) != chat_id:
            return

        await self._repositories.captcha.resolve_pending_sessions(user_id, "expired")

        channel = self._require_channel()
        try:
            await channel.decline_join_request(self._chat_channel(chat_id), user_id)
        except Exception:
            pass

        try:
            await channel.send_text(
                target=self._private_channel(user_id),
                text=(
                    "Время вышло😢\nЗаявка на вступление отклонена...\n"
                    "Ты можешь подать заявку повторно или обратиться к @thetrtk."
                ),
            )
        except Exception:
            pass

        logger.info("Онбординг истёк: user=%d chat=%d", user_id, chat_id)

    async def add_captcha_chat(self, chat_id: int, actor_user_id: int, *, private_chat: bool) -> CaptchaCommandResult:
        """Включить онбординг для текущей беседы."""
        if private_chat:
            return CaptchaCommandResult(True, "Отправь эту команду прямо из нужной беседы.")

        added = await self._repositories.captcha.add_captcha_chat(chat_id, actor_user_id)
        if not added:
            return CaptchaCommandResult(True, "ℹ️ Онбординг уже включён для этой беседы.")
        return CaptchaCommandResult(True, "✅ Онбординг включён для этой беседы.")

    async def remove_captcha_chat(self, chat_id: int, *, private_chat: bool) -> CaptchaCommandResult:
        """Отключить онбординг для текущей беседы."""
        if private_chat:
            return CaptchaCommandResult(True, "Отправь эту команду прямо из нужной беседы.")

        removed = await self._repositories.captcha.remove_captcha_chat(chat_id)
        if removed:
            return CaptchaCommandResult(True, "✅ Онбординг отключён для этой беседы.")
        return CaptchaCommandResult(True, "ℹ️ Онбординг и так не был включён для этой беседы.")

    async def captcha_list_text(self) -> str:
        """Собрать список бесед с включённым онбордингом."""
        chats = await self._repositories.captcha.get_captcha_chats()
        if not chats:
            return "Онбординг не включён ни для одной беседы."

        all_known = {c["chat_id"]: c.get("name") for c in await self._repositories.chats.get_all_chats()}
        lines = ["<b>🔐 Беседы с онбордингом:</b>"]
        for captcha_chat in chats:
            cid = captcha_chat["chat_id"]
            name = all_known.get(cid)
            label = f"{name} (<code>{cid}</code>)" if name else f"<code>{cid}</code>"
            lines.append(label)
        return "\n".join(lines)

    def timeout_text(self) -> str:
        """Текст для /captcha_timeout."""
        return (
            f"⏱ Текущий таймаут онбординга: <b>{self.timeout_str()}</b>\n\n"
            "Для изменения отредактируй переменные в <code>.env</code> или <code>config.py</code>:\n"
            f"<code>ONBOARDING_TIMEOUT_HOURS = {self._settings.timeout_hours}</code>\n"
            f"<code>ONBOARDING_TIMEOUT_MINUTES = {self._settings.timeout_minutes}</code>\n\n"
            f"Задержка перед approve: <b>{self._settings.approve_delay_seconds} сек.</b>\n"
            f"(<code>APPROVE_DELAY_SECONDS = {self._settings.approve_delay_seconds}</code>)"
        )

    def timeout(self) -> timedelta:
        return timedelta(
            hours=self._settings.timeout_hours,
            minutes=self._settings.timeout_minutes,
        )

    def expires_at(self) -> datetime:
        return datetime.now(timezone.utc) + self.timeout()

    def timeout_str(self) -> str:
        total_minutes = self._settings.timeout_hours * 60 + self._settings.timeout_minutes
        if total_minutes < 60:
            return f"{total_minutes} мин."
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if minutes == 0:
            return f"{hours} ч."
        return f"{hours} ч. {minutes} мин."

    async def _send_onboarding_log(
        self,
        user_id: int,
        username: str | None,
        joined_chat_id: int,
        joined_chat_title: str,
        joined_chat_link: str | None,
        onboarding_answer: str | None,
    ) -> None:
        """Отправить лог онбординга во все chats.onboarding_log_enabled."""
        log_chats = await self._repositories.chats.get_onboarding_log_chats()
        if not log_chats:
            return

        if username:
            user_ref = f"@{_escape_html(username)}"
        else:
            user_ref = f'<a href="tg://user?id={user_id}">пользователь</a>'

        if joined_chat_link:
            chat_ref = _html_link(joined_chat_title, joined_chat_link)
        else:
            chat_ref = f"<b>{_escape_html(joined_chat_title)}</b>"

        lines = [f"от {user_ref} вступившего в {chat_ref}"]
        if onboarding_answer:
            lines.append("")
            lines.append(f"<b>О себе:</b> {_escape_html(onboarding_answer)}")
        text = "\n".join(lines)

        channel = self._require_channel()
        for entry in log_chats:
            target_chat_id = int(entry["chat_id"])
            topic_id = entry.get("onboarding_log_topic_id")
            try:
                await channel.send_text(
                    target=ChannelRef(
                        channel="telegram",
                        external_chat_id=target_chat_id,
                        topic_id=topic_id,
                    ),
                    text=text,
                    disable_web_page_preview=True,
                )
                logger.info(
                    "Онбординг-лог отправлен: user=%d → chat=%d topic=%s",
                    user_id,
                    target_chat_id,
                    topic_id,
                )
            except Exception as exc:
                logger.warning(
                    "Не удалось отправить онбординг-лог: user=%d → chat=%d: %s",
                    user_id,
                    target_chat_id,
                    exc,
                )

    def _require_channel(self) -> ChannelPort:
        if self._channel is None:
            raise RuntimeError("CaptchaService channel is required for this action")
        return self._channel

    @staticmethod
    def _is_session_active(session: dict | None) -> bool:
        if not session:
            return False
        expires_at = session["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) <= expires_at

    @staticmethod
    def _private_channel(user_id: int) -> ChannelRef:
        return ChannelRef(channel="telegram", external_chat_id=user_id)

    @staticmethod
    def _chat_channel(chat_id: int) -> ChannelRef:
        return ChannelRef(channel="telegram", external_chat_id=chat_id)


def _escape_html(value: object) -> str:
    if value is None:
        return ""
    return _html_escape(str(value), quote=False)


def _html_link(label: object, url: object) -> str:
    return f'<a href="{_html_escape(str(url), quote=True)}">{_escape_html(label)}</a>'
