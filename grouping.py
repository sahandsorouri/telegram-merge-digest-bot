"""Turn Telegram messages into Items, then group Items by author.

Everything here is duck typed on attribute names, so the tests can feed it
simple namespaces instead of real Telegram objects.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import date, datetime

from config import TIMEZONE
from models import Group, Item


def local_day(ts: int) -> date:
    """The calendar day a message belongs to, in the bot's timezone."""
    return datetime.fromtimestamp(ts, TIMEZONE).date()

# Attachment fields that make a message "not text".
MEDIA_FIELDS = (
    "photo",
    "document",
    "video",
    "audio",
    "voice",
    "video_note",
    "animation",
    "sticker",
    "contact",
    "location",
    "venue",
    "poll",
    "dice",
    "game",
)

UNKNOWN_KEY = "unknown"
UNKNOWN_NAME = "Unknown sender"


def _person_name(user) -> str:
    """Best display name for a Telegram User."""
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = " ".join(p for p in (first, last) if p)
    if full:
        return full
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return f"@{username}"
    return f"User {getattr(user, 'id', '?')}"


def _chat_name(chat, signature: str | None) -> str:
    title = (getattr(chat, "title", "") or "").strip()
    if not title:
        title = (getattr(chat, "username", "") or "").strip() or "Unnamed chat"
    signature = (signature or "").strip()
    return f"{title} ({signature})" if signature else title


def _ts_of(obj, fallback: int) -> int:
    date = getattr(obj, "date", None)
    if date is None:
        return fallback
    try:
        return int(date.timestamp())
    except (AttributeError, TypeError, ValueError, OSError):
        return fallback


def author_of(message) -> tuple[str, str, str, int]:
    """Return (author_key, author_name, author_kind, original_ts).

    Kinds map to Telegram's MessageOrigin types:
      user        the original sender is visible
      hidden      the sender hid their forward link, only a name string arrives
      chat        posted on behalf of a group
      channel     forwarded from a channel
      unknown     no forward_origin at all (typed here, pasted, or forwarded
                  with "hide sender name" ticked)
    """
    now = int(time.time())
    msg_ts = _ts_of(message, now)
    origin = getattr(message, "forward_origin", None)

    if origin is None:
        return UNKNOWN_KEY, UNKNOWN_NAME, "unknown", msg_ts

    ts = _ts_of(origin, msg_ts)
    kind = getattr(origin, "type", None)

    if kind == "user":
        user = getattr(origin, "sender_user", None)
        if user is not None:
            return f"u:{getattr(user, 'id', '?')}", _person_name(user), "user", ts

    elif kind == "hidden_user":
        name = (getattr(origin, "sender_user_name", "") or "").strip()
        if name:
            return f"h:{name.casefold()}", name, "hidden", ts

    elif kind == "chat":
        chat = getattr(origin, "sender_chat", None)
        if chat is not None:
            name = _chat_name(chat, getattr(origin, "author_signature", None))
            return f"c:{getattr(chat, 'id', name)}", name, "chat", ts

    elif kind == "channel":
        chat = getattr(origin, "chat", None)
        if chat is not None:
            name = _chat_name(chat, getattr(origin, "author_signature", None))
            return f"c:{getattr(chat, 'id', name)}", name, "channel", ts

    return UNKNOWN_KEY, UNKNOWN_NAME, "unknown", ts


def has_media(message) -> bool:
    return any(getattr(message, field, None) for field in MEDIA_FIELDS)


def item_from_message(message) -> tuple[Item | None, bool]:
    """Return (item, had_attachment).

    v1 is text only. An attachment is always counted so the digest can report
    how many were dropped, but the file itself is never carried over. A caption
    counts as text and is kept.
    """
    media = has_media(message)
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text:
        return None, media

    key, name, kind, ts = author_of(message)
    return Item(author_key=key, author_name=name, author_kind=kind, ts=ts, text=text, seq=0), media


def canonicalise(items: list[Item]) -> list[Item]:
    """Give the same person one author key.

    Forwarding the same person from two chats can produce one copy with a user
    id and one with only a name, because they hid their forward link. Same
    human, so the name-only copy adopts the visible key.
    """
    visible: dict[str, str] = {}
    for item in items:
        if item.author_kind in ("user", "chat", "channel"):
            visible.setdefault(item.author_name.casefold(), item.author_key)

    aliases = {
        item.author_key: visible[item.author_name.casefold()]
        for item in items
        if item.author_kind == "hidden" and item.author_name.casefold() in visible
    }
    if not aliases:
        return items
    return [replace(i, author_key=aliases.get(i.author_key, i.author_key)) for i in items]


def in_time_order(items: list[Item]) -> list[Item]:
    """Original send time, with arrival order as the tie break."""
    return sorted(items, key=lambda i: (i.ts, i.seq))


def timeline_blocks(items: list[Item]) -> list[Group]:
    """Chronological order, with consecutive messages from one person merged.

    This is the default view. The conversation still reads top to bottom in the
    order it actually happened, and a run of messages from one person collapses
    into a single block instead of repeating their name on every line.

    A block never spans two days. A message at 23:59 and the next at 00:01 are
    different days to a reader, so they get their own blocks and the renderer
    can put a date between them.
    """
    blocks: list[Group] = []
    for item in in_time_order(canonicalise(items)):
        same_author = blocks and blocks[-1].key == item.author_key
        same_day = same_author and local_day(blocks[-1].items[-1].ts) == local_day(item.ts)
        if same_day:
            blocks[-1].items.append(item)
            continue
        blocks.append(
            Group(key=item.author_key, name=item.author_name, kind=item.author_kind, items=[item])
        )
    return blocks


def group_items(items: list[Item]) -> list[Group]:
    """Everything from one person in a single block, time order lost.

    Ordered by when each person first appears. Messages inside a block stay in
    original chronological order.
    """
    groups: dict[str, Group] = {}
    for item in canonicalise(items):
        group = groups.get(item.author_key)
        if group is None:
            group = Group(key=item.author_key, name=item.author_name, kind=item.author_kind)
            groups[item.author_key] = group
        group.items.append(item)

    ordered = list(groups.values())
    for group in ordered:
        group.items = in_time_order(group.items)
    ordered.sort(key=lambda g: (g.first_ts, g.items[0].seq if g.items else 0))
    return ordered
