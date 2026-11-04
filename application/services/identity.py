"""Private sender identities used in DM and reply rendering."""
from typing import Any


async def sender_label_for_chat(
    dm_repository: Any,
    chat_type: str,
    user_id: int | None,
    visible_label: str,
) -> str:
    """Never expose user details from private chats while /anon is enabled.

    Unknown sessions preserve previous behavior (non-anonymous). Lookup errors
    must propagate: do not silently reveal an identity if privacy cannot be checked.
    """
    if chat_type != "private" or user_id is None:
        return visible_label
    session = await dm_repository.get_dm_session(user_id)
    return "анонима" if session and session.get("anonymous", False) else visible_label
