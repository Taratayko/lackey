"""Bounded in-process collection of Telegram media_group events.

One aiogram update arrives per album item. Collect a short burst, then perform
one logical delivery so ALL source/destination message IDs share one mapping.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from aiogram import Bot, F, Router
from aiogram.types import Message

from adapters.telegram import TelegramChannelAdapter, telegram_message_to_attachments
from application.services import RelayService, RelayTextRequest, ReplyRelayRequest, ReplyService
from application.services.identity import sender_label_for_chat
from database import db
from .common import extract_target_only, resolve_chat_and_text, resolve_target_only, sender_mention

router = Router()
logger = logging.getLogger(__name__)
ALBUM_QUIET_SECONDS = 1.0
MAX_PENDING_ALBUMS = 256


@dataclass
class _AlbumBucket:
    bot: Bot
    items: dict[int, Message] = field(default_factory=dict)
    task: asyncio.Task | None = None


_buckets: dict[tuple[int, str], _AlbumBucket] = {}
_guard = asyncio.Lock()


@router.message(F.media_group_id)
async def collect_album(msg: Message, bot: Bot) -> None:
    key = (msg.chat.id, str(msg.media_group_id))
    async with _guard:
        bucket = _buckets.get(key)
        if bucket is None:
            if len(_buckets) >= MAX_PENDING_ALBUMS:
                logger.error("Album queue at capacity; dropping media_group=%s", key)
                await msg.reply("Не доставлено 💔 Очередь альбомов переполнена.")
                return
            bucket = _AlbumBucket(bot)
            _buckets[key] = bucket
            bucket.task = asyncio.create_task(_flush_album(key))
        bucket.items[msg.message_id] = msg


async def _flush_album(key: tuple[int, str]) -> None:
    await asyncio.sleep(ALBUM_QUIET_SECONDS)
    async with _guard:
        bucket = _buckets.pop(key, None)
    if not bucket:
        return
    items = sorted(bucket.items.values(), key=lambda msg: msg.message_id)
    try:
        await relay_album(items, bucket.bot)
    except Exception:
        logger.exception("Album relay failed for %s", key)
        try:
            await items[0].reply("Не доставлено 💔 Ошибка обработки альбома.")
        except Exception:
            logger.exception("Unable to notify album sender")


async def relay_album(items: list[Message], bot: Bot) -> None:
    """Dispatch either a routed album or an album reply to the common services."""
    if not items:
        return
    command = next((item for item in items if (item.caption or "").strip()), None)
    first = command or items[0]
    attachments = tuple(att for msg in items for att in telegram_message_to_attachments(msg))
    if not attachments:
        return
    ids = tuple(msg.message_id for msg in items)
    replied = next((msg.reply_to_message for msg in items if msg.reply_to_message), None)
    if replied:
        me = await bot.get_me()
        if replied.from_user and replied.from_user.id == me.id:
            repos = db.get_repositories()
            label = await sender_label_for_chat(
                repos.dm, first.chat.type,
                first.from_user.id if first.from_user else None,
                sender_mention(first),
            )
            result = await ReplyService(repos, TelegramChannelAdapter(bot)).relay_reply(
                ReplyRelayRequest(
                    current_chat_id=first.chat.id,
                    current_message_id=first.message_id,
                    current_topic_id=first.message_thread_id,
                    current_message_ids=ids,
                    replied_message_id=replied.message_id,
                    sender_label=label,
                    text=first.caption,
                    attachments=attachments,
                )
            )
            if result.user_error:
                await first.reply(result.user_error)
            return
        # Album caption used as a forward command is intentionally ignored:
        # no "Не доставлено ... без альбома" error is posted.
        return

    source_text = first.caption or ""
    if not source_text:
        return
    target = await resolve_target_only(source_text) if extract_target_only(source_text) else None
    extra = ""
    if not target:
        resolved = await resolve_chat_and_text(source_text)
        if resolved:
            target, extra = resolved
    if not target:
        return
    request = RelayTextRequest(
        origin_chat_id=first.chat.id,
        origin_message_id=first.message_id,
        origin_message_ids=ids,
        origin_topic_id=first.message_thread_id,
        target_chat=target,
        text=extra,
        sender_label=sender_mention(first),
        sender_user_id=first.from_user.id if first.from_user else None,
        attachments=attachments,
    )
    service = RelayService(db.get_repositories(), TelegramChannelAdapter(bot))
    result = (await service.relay_dm(request) if first.chat.type == "private"
              else await service.relay_group(request))
    if result.user_error:
        await first.reply(result.user_error)
