"""Local persistence for the real-time poller's Gmail history cursor.

Replaces the old Sheets-backed ``Settings`` row. A Gmail history id is not
sensitive, so this is a small plain JSON file rather than an encrypted one
(contrast :mod:`app.gmail.tokens`). It lives next to the OAuth token file for
the same reason both need durability: ``.github/workflows/realtime-poll.yml``
runs this on a fresh GitHub Actions runner every time, so nothing written
here survives past the end of that one run *unless something commits it back
to the repo* — which is exactly what that workflow's last step does with
this file (the OAuth token instead re-seeds itself from a repo secret each
run; see :mod:`app.gmail.tokens`). A missing file — the very first run, or a
run whose commit step failed — just means the next poll re-bootstraps from
"now": a brief gap in real-time coverage, not a safety issue. That's the same
trade-off a fresh Gmail connection already accepts on its first-ever poll.
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
