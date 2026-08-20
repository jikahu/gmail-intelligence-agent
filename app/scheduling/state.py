"""Local persistence for the real-time poller's Gmail history cursor.

Replaces the old Sheets-backed ``Settings`` row. A Gmail history id is not
sensitive, so this is a small plain JSON file rather than an encrypted one
(contrast :mod:`app.gmail.tokens`). It lives next to the OAuth token file for
the same reason: on Render's free plan the local filesystem doesn't survive a
redeploy, so a missing file after one just means the next poll re-bootstraps
from "now" — a brief gap in real-time coverage across a redeploy, not a
safety issue. That's the same trade-off a fresh Gmail connection already
accepts on its first-ever poll.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.logging_config import get_logger

log = get_logger("app.scheduling.state")

STATE_FILE = Path("oauth_tokens") / "realtime_cursor.json"


def load_cursor() -> str | None:
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("realtime_cursor_unreadable", extra={"error": str(exc)})
        return None
    value = data.get("history_id")
    return str(value) if value else None


def save_cursor(history_id: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"history_id": history_id}), encoding="utf-8")


__all__ = ("STATE_FILE", "load_cursor", "save_cursor")
