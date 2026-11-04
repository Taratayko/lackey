"""Pure routing decision for a Telegram reply with an explicit chat prefix.

An explicit `Chat!` command ALWAYS means forward the replied-to message,
regardless of who authored it. Ordinary replies to bot messages stay replies.
No Telegram or Mongo imports in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class ForwardIntent:
    destination_name: str
    comment: str = ""


class ReplyAction(str, Enum):
    FORWARD = "forward"
    REPLY = "reply"
    IGNORE = "ignore"


def parse_forward_intent(text: str | None) -> ForwardIntent | None:
    """`Chat!` and `Chat! comment`; not plain arbitrary reply text.

    Only the first exclamation mark separates the destination name from text;
    multi-word destination names and exclamation marks in comments work.
    """
    if not text or "!" not in text:
        return None
    name, _, comment = text.partition("!")
    name = name.strip()
    if not name or "\n" in name or name.startswith("/"):
        return None
    return ForwardIntent(name, comment.strip())


def decide_reply_action(text: str | None, replied_by_bot: bool) -> ReplyAction:
    if parse_forward_intent(text):
        return ReplyAction.FORWARD
    return ReplyAction.REPLY if replied_by_bot else ReplyAction.IGNORE
