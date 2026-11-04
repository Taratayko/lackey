"""Access checks for resolved delivery targets (after redirect)."""
from typing import Any


async def resolve_destination(chats: Any, target: dict) -> dict:
    """Resolve one configured redirect, preserving legacy one-hop semantics."""
    if target.get("redirect_to"):
        redirected = await chats.get_chat_by_id(target["redirect_to"])
        if redirected:
            return redirected
    return target


def group_destination_allowed(source: dict, requested: dict, resolved: dict) -> bool:
    """Deny paths to chief chats regardless of a redirect at either endpoint."""
    if source.get("group") == "special":
        return False
    return bool(source.get("is_chief") or not (
        requested.get("is_chief") or resolved.get("is_chief")
    ))


def dm_destination_allowed(requested: dict, resolved: dict) -> bool:
    """Private messages may never reach a chief chat, including by redirect."""
    return not (requested.get("is_chief") or resolved.get("is_chief"))
