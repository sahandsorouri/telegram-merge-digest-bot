"""Plain data structures. No Telegram objects live in here."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Item:
    """One forwarded message, reduced to what the digest needs."""

    author_key: str
    author_name: str
    author_kind: str  # user | hidden | chat | channel | unknown
    ts: int  # original send time, epoch seconds, UTC
    text: str
    seq: int  # arrival order, used as a tie break

    def to_dict(self) -> dict:
        return {
            "author_key": self.author_key,
            "author_name": self.author_name,
            "author_kind": self.author_kind,
            "ts": self.ts,
            "text": self.text,
            "seq": self.seq,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Item":
        return cls(
            author_key=raw["author_key"],
            author_name=raw["author_name"],
            author_kind=raw["author_kind"],
            ts=int(raw["ts"]),
            text=raw["text"],
            seq=int(raw["seq"]),
        )


@dataclass
class Group:
    """All messages from one author, in original order."""

    key: str
    name: str
    kind: str
    items: list[Item] = field(default_factory=list)

    @property
    def first_ts(self) -> int:
        return min(i.ts for i in self.items) if self.items else 0


@dataclass
class Batch:
    """One collecting session. Stays open until Done is tapped."""

    batch_id: str
    chat_id: int
    items: list[Item] = field(default_factory=list)
    skipped_media: int = 0
    status_message_id: int | None = None
    created_ts: int = field(default_factory=lambda: int(time.time()))
    closed: bool = False
    closed_ts: int = 0

    @property
    def count(self) -> int:
        return len(self.items)

    def add(self, item: Item) -> None:
        item.seq = len(self.items)
        self.items.append(item)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "chat_id": self.chat_id,
            "items": [i.to_dict() for i in self.items],
            "skipped_media": self.skipped_media,
            "status_message_id": self.status_message_id,
            "created_ts": self.created_ts,
            "closed": self.closed,
            "closed_ts": self.closed_ts,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Batch":
        return cls(
            batch_id=raw["batch_id"],
            chat_id=int(raw["chat_id"]),
            items=[Item.from_dict(i) for i in raw.get("items", [])],
            skipped_media=int(raw.get("skipped_media", 0)),
            status_message_id=raw.get("status_message_id"),
            created_ts=int(raw.get("created_ts", 0)),
            closed=bool(raw.get("closed", False)),
            closed_ts=int(raw.get("closed_ts", 0)),
        )
