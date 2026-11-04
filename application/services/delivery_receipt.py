"""Post-send bookkeeping. A Mongo/reaction error must NEVER re-send a message."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

MAPPING_WARNING = "Доставлено, но связь для ответов не сохранена. Не отправляйте сообщение повторно."


async def finalize_delivery(
    save_mapping: Callable[[], Awaitable[object]],
    increment_statistics: Callable[[], Awaitable[object]] | None = None,
    mark_reaction: Callable[[], Awaitable[object]] | None = None,
) -> bool:
    """Perform independent follow-ups, logging failures; True if reply mapping saved."""
    mapping_saved = True
    try:
        await save_mapping()
    except Exception:
        mapping_saved = False
        logger.exception("Delivered, but thread mapping persistence failed")

    if increment_statistics is not None:
        try:
            await increment_statistics()
        except Exception:
            logger.exception("Delivered, but statistics update failed")

    if mark_reaction is not None:
        try:
            await mark_reaction()
        except Exception:
            logger.exception("Delivered, but reaction update failed")
    return mapping_saved
