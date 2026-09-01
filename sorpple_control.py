"""
Control channel between the web dashboard and the running bot.

The dashboard is a separate process from sorpple.py, but the things it needs to do
-- pause a poller, change an interval, force a cycle -- can only be done by the
process that owns the discord.py loop objects.  Rather than put an HTTP server
inside the bot (where a web bug could take the bot down), the two processes pass
messages through two small JSON files:

    sorpple_control.json    dashboard -> bot   queued actions
    sorpple_status.json     bot -> dashboard   live telemetry heartbeat

The bot drains the queue every couple of seconds and applies each action through
the same code path the slash commands use, so /status in Discord and the website
always agree.

Both files are best-effort: if the bot is not running the queue simply accumulates
and the dashboard reports it offline.  Neither file is authoritative state --
sorpple_settings.json remains the source of truth for intervals and paused flags.
"""

import json
import os
import time
from datetime import datetime, timezone

from prosple_monitor import SCRIPT_DIR

CONTROL_FILE = os.path.join(SCRIPT_DIR, "sorpple_control.json")
STATUS_FILE  = os.path.join(SCRIPT_DIR, "sorpple_status.json")

VALID_SOURCES = ("prosple", "jobstreet", "indeed", "all")
VALID_ACTIONS = ("interval", "pause", "resume", "poll", "monitor")

# The bot rewrites the status heartbeat every drain tick.  Past this, assume the
# process is gone rather than showing stale intervals as if they were live.
STATUS_STALE_SECONDS = 30


def _read_json(path: str, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return fallback


def _write_json(path: str, data) -> None:
    """Atomic write — a reader must never see a half-written queue."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


# ── Dashboard side ────────────────────────────────────────────────────────────


def enqueue(action: str, source: str = "all", value=None) -> dict:
    """Queue one action for the bot.  Returns the queued entry.

    Raises ValueError on an unknown action or source so the API can answer 400
    rather than writing something the bot will silently skip.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown source {source!r}")

    entry = {
        "action":    action,
        "source":    source,
        "value":     value,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "id":        f"{time.time_ns()}",
    }
    queue = _read_json(CONTROL_FILE, {"queue": []})
    if not isinstance(queue, dict) or not isinstance(queue.get("queue"), list):
        queue = {"queue": []}
    queue["queue"].append(entry)
    _write_json(CONTROL_FILE, queue)
    return entry


def read_status() -> dict | None:
    """Live telemetry from the bot, or None if it is not running.

    A heartbeat older than STATUS_STALE_SECONDS means the bot died without
    clearing the file, so treat it as offline.
    """
    data = _read_json(STATUS_FILE, None)
    if not isinstance(data, dict) or "published_at" not in data:
        return None
    try:
        published = datetime.fromisoformat(data["published_at"])
    except (ValueError, TypeError):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - published).total_seconds()
    if age > STATUS_STALE_SECONDS:
        return None
    return data


# ── Bot side ──────────────────────────────────────────────────────────────────


def drain() -> list[dict]:
    """Take every queued action and clear the queue.

    Clearing by rewriting the whole file (rather than removing entries one by one)
    keeps this a single atomic replace; an action queued during the drain lands in
    the next tick instead of being lost.
    """
    queue = _read_json(CONTROL_FILE, {"queue": []})
    if not isinstance(queue, dict):
        queue = {"queue": []}
    actions = queue.get("queue") or []
    if not isinstance(actions, list):
        actions = []
    if actions:
        _write_json(CONTROL_FILE, {"queue": []})
    return [a for a in actions if isinstance(a, dict)]


def publish_status(payload: dict) -> None:
    """Write the telemetry heartbeat the dashboard reads."""
    payload = dict(payload)
    payload["published_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(STATUS_FILE, payload)


def clear_status() -> None:
    """Drop the heartbeat on a clean shutdown so the dashboard shows offline now."""
    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except OSError:
        pass
