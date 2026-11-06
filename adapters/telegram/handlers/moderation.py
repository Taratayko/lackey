"""Telegram handlers: /ban, /unban, /mute, /unmute, /banlist и их текстовые формы.

Работают только в группах. Транспортный слой: разбирает reply/аргументы и
отвечает пользователю; правила, права и обращение к Telegram — в
application.services.moderation.
"""
import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from adapters.telegram import TelegramChannelAdapter
from application.services import (
    ModerationRequest,
    ModerationService,
    ModerationTarget,
    parse_mute_args,
)
from config import settings
from database import db

from .common import is_real_reply

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

# Слова-триггеры без «/»: «мут 2ч спам» ответом на сообщение. Сообщения с «!»
# не трогаем — это формат ретрансляции «НазваниеКоммуны! текст».
_TEXT_ACTIONS = {"бан": "ban", "разбан": "unban", "мут": "mute", "размут": "unmute"}

_USAGE = {
    "ban": "Используй reply на сообщение нарушителя: /ban [причина]",
    "mute": "Используй reply на сообщение нарушителя: /mute [срок] [причина]\n"
            "Срок: 30 минут, 2 часа, 1 день, 1 неделя или «навсегда» "
            "(можно коротко: 30м, 2ч, 1д, 1н). Без срока — 1 час",
    "unban": "Использование: /unban @username (или reply на сообщение)",
    "unmute": "Использование: /unmute @username (или reply на сообщение)",
}

_USER_ID_RE = re.compile(r"^\d{5,}$")


def _moderation_service(bot: Bot) -> ModerationService:
    """Создать сервис модерации для текущего Telegram runtime."""
    return ModerationService(
        repositories=db.get_repositories(),
        channel=TelegramChannelAdapter(bot),
        owner_user_id=settings.ADMIN_ID,
    )


def _actor(msg: Message) -> tuple[int | None, bool]:
    """(user_id, анонимный_админ). Анонимный админ пишет от имени самой группы."""
    if msg.sender_chat and msg.sender_chat.id == msg.chat.id:
        return None, True
    if msg.from_user:
        return msg.from_user.id, False
    return None, False


async def is_moderator(bot: Bot, msg: Message) -> bool:
    """Может ли отправитель банить/мутить в этом чате (для выбора версии /help)."""
    actor_id, anonymous = _actor(msg)
    return await _moderation_service(bot).is_authorized(msg.chat.id, actor_id, anonymous)


def _target_from_reply(msg: Message) -> tuple[ModerationTarget | None, str | None]:
    """Цель из reply: (цель, None), (None, ошибка) или (None, None), если reply нет."""
    if not is_real_reply(msg):
        return None, None
    replied = msg.reply_to_message
    if replied.sender_chat:
        return None, "❌ Это сообщение от канала или анонимного админа — выберите сообщение пользователя"
    user = replied.from_user
    if not user:
        return None, "❌ Не удалось определить автора сообщения"
    return ModerationTarget(user.id, user.username, user.full_name), None


def _split_first(args: str) -> tuple[str, str]:
    head, _, tail = args.strip().partition(" ")
    return head, tail.strip()


async def _run(msg: Message, bot: Bot, action: str, args: str, *, silent: bool) -> None:
    actor_id, anonymous = _actor(msg)
    if actor_id is None and not anonymous:
        return
    service = _moderation_service(bot)

    if silent and not await service.is_authorized(msg.chat.id, actor_id, anonymous):
        # Обычное слово в чате — не наша команда: отдаём сообщение остальным хэндлерам.
        raise SkipHandler

    if action in ("banlist", "mutelist"):
        listing = service.banlist_text if action == "banlist" else service.mutelist_text
        result = await listing(msg.chat.id, actor_id, anonymous)
        if result.user_message:
            await msg.reply(result.user_message)
        return

    target, error = _target_from_reply(msg)
    if error:
        await msg.reply(error)
        return

    rest = args.strip()
    user_id: int | None = target.user_id if target else None
    username: str | None = None

    if target is None:
        head, tail = _split_first(args)
        if _USER_ID_RE.match(head):
            user_id, rest = int(head), tail
        elif head.startswith("@") and action in ("unban", "unmute"):
            username, rest = head.lstrip("@"), tail
        else:
            await msg.reply(_USAGE[action])
            return
    elif action in ("unban", "unmute"):
        username = target.username

    if action in ("unban", "unmute"):
        method = service.unban if action == "unban" else service.unmute
        result = await method(
            msg.chat.id, actor_id, user_id=user_id, username=username, anonymous_admin=anonymous,
        )
    else:
        request = ModerationRequest(
            chat_id=msg.chat.id,
            actor_user_id=actor_id,
            target=target or ModerationTarget(user_id),
            anonymous_admin=anonymous,
            reply_message_id=msg.reply_to_message.message_id if target else None,
        )
        if action == "ban":
            result = await service.ban(request, reason=rest or None)
        else:
            parsed = parse_mute_args(rest)
            result = await service.mute(request, parsed.duration, parsed.reason)

    if result.user_message:
        await msg.reply(result.user_message)


@router.message(Command("ban", "unban", "mute", "unmute", "banlist", "mutelist"))
async def cmd_moderation(msg: Message, bot: Bot, command: CommandObject):
    await _run(msg, bot, command.command, command.args or "", silent=False)


def _text_action(msg: Message) -> dict[str, str] | bool:
    if not msg.text or "!" in msg.text:
        return False
    parts = msg.text.strip().split(maxsplit=1)
    action = _TEXT_ACTIONS.get(parts[0].lower())
    if not action:
        return False
    return {"action": action, "args": parts[1] if len(parts) > 1 else ""}


@router.message(F.text, _text_action)
async def text_moderation(msg: Message, bot: Bot, action: str, args: str):
    await _run(msg, bot, action, args, silent=True)


class MessageLogMiddleware(BaseMiddleware):
    """Запоминает id сообщений участников групп — чтобы зачистить их при бане.

    Подключается на корневой роутер (handlers/__init__.py) и ничего не
    потребляет: любая ошибка журнала только логируется, сообщение идёт дальше.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        try:
            user = event.from_user
            if (
                event.chat.type in ("group", "supergroup")
                and user and not user.is_bot
                and not event.sender_chat
            ):
                log = db.get_repositories().message_log
                if log is not None:
                    await log.save(event.chat.id, user.id, event.message_id)
        except Exception:
            logger.warning("Message log write failed", exc_info=True)
        return await handler(event, data)
