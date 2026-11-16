"""Environment and constants."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()



def parse_ids(raw: str) -> frozenset[int]:
    """Comma separated numeric Telegram user ids; anything else is skipped."""
    ids = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return frozenset(ids)


# Default deny: only these Telegram user ids may use the bot.
# Empty means the bot answers nobody.
ALLOWED_USER_IDS = parse_ids(os.getenv("ALLOWED_USER_IDS", ""))


def is_allowed(user_id, allowed: frozenset[int] | None = None) -> bool:
    ids = ALLOWED_USER_IDS if allowed is None else allowed
    return isinstance(user_id, int) and user_id in ids


# IANA time zone used for times and dates in the digest, for example Europe/London.
TIMEZONE = ZoneInfo(os.getenv("TZ", "UTC") or "UTC")

# Seconds of silence before the status message is re-posted at the bottom.
# This only controls when the status line refreshes. It never closes a batch.
STATUS_DEBOUNCE = float(os.getenv("STATUS_DEBOUNCE", "1.2"))

# Safety valve. A single batch larger than this stops accepting new messages.
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "500"))

# Finished batches kept per person, so the format buttons keep working.
# Per person, not global, otherwise a busy user evicts everyone else's results.
KEEP_FINISHED = int(os.getenv("KEEP_FINISHED", "5"))

# Nothing is kept forever. The bot holds other people's conversations,
# so delivered batches are deleted on a timer and abandoned ones expire.
RETENTION_HOURS = float(os.getenv("RETENTION_HOURS", "24"))
STALE_BATCH_HOURS = float(os.getenv("STALE_BATCH_HOURS", "168"))
PURGE_EVERY_SECONDS = int(os.getenv("PURGE_EVERY_SECONDS", "3600"))

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "state.json"

# Hard Telegram limits.
TELEGRAM_TEXT_LIMIT = 4096
