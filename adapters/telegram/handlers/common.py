"""Shared Telegram handler helpers.

Здесь остаются только transport-specific утилиты Telegram-router слоя:
парсинг имени коммуны из текста, определение медиа и проверка настоящего reply
в форумных топиках. Бизнес-логика остаётся в application/services.
"""
from aiogram.types import InputRichMessage, Message

from database import db


async def send_rich(
    msg: Message, html: str, topic_id: int | None = None, reply_markup=None,
) -> Message:
    """Отправить rich-message (Bot API 10.1+, sendRichMessage; нужен aiogram >= 3.29).

    В отличие от parse_mode="HTML", rich-message поддерживает настоящие
    заголовки <h1>-<h6>. Если topic_id не передан — отвечаем в тот же топик,
    откуда пришла команда, а не в General.
    """
    kwargs: dict = {
        "chat_id": msg.chat.id,
        "rich_message": InputRichMessage(html=html),
    }
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    effective_topic_id = topic_id if topic_id is not None else msg.message_thread_id
    if effective_topic_id:
        kwargs["message_thread_id"] = effective_topic_id
    return await msg.bot.send_rich_message(**kwargs)


def sender_mention(msg: Message) -> str:
    """Вернуть обычный (не HTML) @username или имя отправителя.

    HTML-escaping выполняется ровно один раз в presentation.formatters.
    """
    user = msg.from_user
    if not user:
        return "Неизвестный"
    if user.username:
        return f"@{user.username}"
    return user.full_name or "Неизвестный"


def has_media(msg: Message) -> bool:
    """True, если в сообщении есть поддерживаемое медиа."""
    return bool(
        msg.photo or msg.video or msg.document or msg.audio or
        msg.voice or msg.video_note or msg.sticker or msg.animation
    )


def extract_target_and_text(text: str) -> tuple[str, str] | None:
    """
    Парсит формат «ИмяЧата! текст».
    Возвращает (target_name, message_text) или None.
    Поддерживает многословные имена — граница имени однозначно задаётся знаком «!».
    """
    stripped = text.strip()
    if "!" not in stripped:
        return None
    name_part, _, rest = stripped.partition("!")
    name_part = name_part.strip()
    rest = rest.strip()
    if not name_part or not rest:
        return None
    return name_part, rest


def extract_target_only(text: str) -> str | None:
    """
    Парсит формат «ИмяЧата!» (восклицательный знак после имени, ничего больше).
    Используется когда пользователь хочет отправить только медиа без текста.
    Возвращает имя цели или None.
    """
    stripped = text.strip()
    if "!" not in stripped:
        return None
    name_part, _, rest = stripped.partition("!")
    name_part = name_part.strip()
    rest = rest.strip()
    if not name_part or rest:
        return None
    return name_part


async def resolve_target(text: str) -> tuple[dict, str] | None:
    """
    Ищет коммуну в тексте формата «ИмяЧата! текст».
    Граница имени однозначно задаётся знаком «!», поэтому многословные
    имена коммун поддерживаются без перебора префиксов.
    Возвращает (chat_doc, remaining_text) или None если коммуна не найдена.

    Пример: «Могильный Платон! привет» → ('...doc...', 'привет')
    """
    parsed = extract_target_and_text(text)
    if not parsed:
        return None
    name, rest = parsed
    chat = await db.get_chat_by_name(name)
    if chat:
        return chat, rest
    return None


async def resolve_target_only(text: str) -> dict | None:
    """
    Ищет коммуну в тексте когда текст = «НазваниеКоммуны!» (без остатка).
    Возвращает chat_doc или None.
    Поддерживает многословные имена коммун (граница задаётся знаком «!»).
    """
    name = extract_target_only(text)
    if not name:
        # На случай если передали уже «голое» имя без «!» на конце
        name = text.strip().rstrip("!").strip()
    if not name:
        return None
    return await db.get_chat_by_name(name)


def text_as_written(source: str | None) -> str:
    """Текст для пересылки в исходном виде: вместе с названием/алиасом коммуны.

    «Всё просто! Хай!» уходит как «Всё просто! Хай!», «Простые! Хай!» — как
    «Простые! Хай!» (то, что именно написал отправитель, без нормализации).
    """
    return (source or "").strip()


async def resolve_chat_and_text(full_text: str, caption: str | None = None) -> tuple[dict, str] | None:
    """Разобрать текст команды и вернуть (chat_doc, text_as_written) или None.

    Второй элемент — исходный текст целиком, включая «ИмяКоммуны!» так, как
    его написал отправитель (см. text_as_written).
    """
    source = full_text or caption or ""
    if not source:
        return None

    parsed = extract_target_and_text(source)
    resolved = None

    if parsed:
        target_name_single, text_single = parsed
        target_chat_single = await db.get_chat_by_name(target_name_single)
        if target_chat_single:
            resolved = (target_chat_single, text_single)

    if not resolved:
        resolved = await resolve_target(source)

    if resolved:
        return resolved[0], text_as_written(source)
    return None


def is_real_reply(msg: Message) -> bool:
    """True только если это настоящий reply, а не автоматический topic header."""
    if not msg.reply_to_message:
        return False
    replied = msg.reply_to_message

    if getattr(replied, "forum_topic_created", None):
        return False

    if (
        msg.message_thread_id
        and replied.message_id == msg.message_thread_id
        and not replied.text
        and not replied.caption
        and not has_media(replied)
    ):
        return False
    return True
