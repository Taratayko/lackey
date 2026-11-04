"""Stable, platform-qualified identity for one logical outbound delivery."""
from __future__ import annotations

import hashlib
import json

from domain.models import ChannelRef, MessageRef


def delivery_id(origin: MessageRef, target: ChannelRef, origin_message_ids=()) -> str:
    """Do not include mutable target topic, text, or personal data in the key."""
    ids = sorted({str(origin.message_id), *(str(item) for item in origin_message_ids)})
    payload = (
        origin.channel, origin.chat_id, ids,
        target.channel.value, target.chat_id,
    )
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
