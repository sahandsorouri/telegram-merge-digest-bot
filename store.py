"""Batch state, with a JSON snapshot so a restart mid-batch loses nothing."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

from config import KEEP_FINISHED, RETENTION_HOURS, STALE_BATCH_HOURS, STATE_FILE
from models import Batch

log = logging.getLogger(__name__)


class Store:
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self.open_batches: dict[int, Batch] = {}
        self.finished: dict[str, Batch] = {}
        self.load()

    # collecting -------------------------------------------------------

    def get_open(self, chat_id: int) -> Batch | None:
        return self.open_batches.get(chat_id)

    def open_new(self, chat_id: int) -> Batch:
        batch = Batch(batch_id=uuid.uuid4().hex[:10], chat_id=chat_id)
        self.open_batches[chat_id] = batch
        return batch

    def get_or_open(self, chat_id: int) -> Batch:
        return self.open_batches.get(chat_id) or self.open_new(chat_id)

    def close(self, chat_id: int) -> Batch | None:
        batch = self.open_batches.pop(chat_id, None)
        if batch is None:
            return None
        batch.closed = True
        batch.closed_ts = int(time.time())
        self.finished[batch.batch_id] = batch
        self._trim()
        return batch

    def discard(self, chat_id: int) -> Batch | None:
        return self.open_batches.pop(chat_id, None)

    def get_finished(self, batch_id: str) -> Batch | None:
        return self.finished.get(batch_id)

    def _trim(self) -> None:
        """Keep at most KEEP_FINISHED results per person.

        Per person on purpose. A global cap would let one heavy user push
        everyone else's results out, and their format buttons would go dead.
        """
        by_chat: dict[int, list[Batch]] = {}
        for batch in self.finished.values():
            by_chat.setdefault(batch.chat_id, []).append(batch)
        for batches in by_chat.values():
            batches.sort(key=lambda b: (b.closed_ts or b.created_ts), reverse=True)
            for extra in batches[KEEP_FINISHED:]:
                self.finished.pop(extra.batch_id, None)

    def purge(self, now: int | None = None) -> int:
        """Delete delivered batches past retention and abandoned open ones.

        This bot can hold other people's forwarded conversations, so nothing
        is kept indefinitely.
        """
        now = now if now is not None else int(time.time())
        removed = 0

        for batch_id, batch in list(self.finished.items()):
            age = now - (batch.closed_ts or batch.created_ts)
            if age > RETENTION_HOURS * 3600:
                self.finished.pop(batch_id, None)
                removed += 1

        for chat_id, batch in list(self.open_batches.items()):
            if now - batch.created_ts > STALE_BATCH_HOURS * 3600:
                self.open_batches.pop(chat_id, None)
                removed += 1

        return removed

    # persistence ------------------------------------------------------

    def save(self) -> None:
        payload = {
            "open": [b.to_dict() for b in self.open_batches.values()],
            "finished": [b.to_dict() for b in self.finished.values()],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("could not write state: %s", exc)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("could not read state, starting empty: %s", exc)
            return
        for raw in payload.get("open", []):
            try:
                batch = Batch.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            # The old status message is stale after a restart. Drop the id so
            # the next forward posts a fresh one instead of editing a ghost.
            batch.status_message_id = None
            self.open_batches[batch.chat_id] = batch
        for raw in payload.get("finished", []):
            try:
                batch = Batch.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            self.finished[batch.batch_id] = batch
        self.purge()
        self._trim()
