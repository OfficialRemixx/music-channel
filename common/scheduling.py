"""Section 5.8: picks the next publish_at slot from config.yaml's
publish_slots_utc, so an upload lands at one of the configured times of day
instead of going public (or sitting unscheduled) the instant it's uploaded."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def next_publish_slot_utc(cfg: dict, now: datetime | None = None) -> str:
    """Returns an RFC3339 UTC timestamp (what YouTube's status.publishAt
    expects) for the next configured slot strictly after `now`."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    slots = sorted(cfg.get("publish_slots_utc") or ["00:00"])
    today = now.date()

    for slot in slots:
        hour, minute = (int(part) for part in slot.split(":"))
        candidate = datetime(today.year, today.month, today.day, hour, minute, tzinfo=timezone.utc)
        if candidate > now:
            return candidate.isoformat().replace("+00:00", "Z")

    # Every slot today has already passed -- use tomorrow's first slot.
    tomorrow = today + timedelta(days=1)
    hour, minute = (int(part) for part in slots[0].split(":"))
    nxt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=timezone.utc)
    return nxt.isoformat().replace("+00:00", "Z")
