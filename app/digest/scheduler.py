"""The always-on side of Phase 14: a background loop that checks, every
:data:`DIGEST_CHECK_INTERVAL_SECONDS`, whether it's time to build today's
digest in the configured timezone — and if so, builds it once and records a
summary row in ``Digest_Log``.

Mirrors ``app/scheduling/service.py``'s ``RealTimePoller`` almost exactly,
with one difference in what "due" means: that loop reacts to new Gmail
history; this one reacts to the clock. The check itself is cheap (just a
timezone-aware comparison) — no Gmail or Sheets call happens unless it's
actually time to build a digest, so waking up every five minutes to look at
the clock costs nothing. Started from FastAPI's startup event, gated on
``settings.digest_scheduler_enabled`` (default ``True`` — unlike real-time
processing, this never writes to Gmail and never spends AI budget, so it
doesn't carry the same reasons to default off; see ``docs/TECHNICAL_STATUS.md``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from app.google_api import NotConnectedError
from app.logging_config import get_logger

log = get_logger("app.digest.scheduler")

#: How often the loop wakes up to check the clock.
DIGEST_CHECK_INTERVAL_SECONDS = 300


@dataclass
class DigestStatus:
    """A snapshot for ``GET /digest/status`` — no message content, just a
    result label and counts, matching ``RealTimeStatus``'s own shape."""

    running: bool = False
    check_count: int = 0
    last_check_at: str | None = None
    #: "generated" | "already_done" | "not_yet_due" | "not_connected" | "error"
    last_result: str | None = None
    last_error: str | None = None
    last_digest_date: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "check_count": self.check_count,
            "last_check_at": self.last_check_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "last_digest_date": self.last_digest_date,
        }


class DigestScheduler:
    """Owns the background asyncio task. One instance lives on the FastAPI
    app (``app.state.digest_scheduler``) for the process's lifetime."""

    def __init__(self, check_interval_seconds: int = DIGEST_CHECK_INTERVAL_SECONDS) -> None:
        self.check_interval_seconds = check_interval_seconds
        self.status = DigestStatus()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self.status.running = True
        self._task = asyncio.create_task(self._loop(), name="digest-scheduler")
        log.info(
            "digest_scheduler_started",
            extra={"check_interval_seconds": self.check_interval_seconds},
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self.status.running = False
        log.info("digest_scheduler_stopped")

    async def _loop(self) -> None:
        while True:
            await self.run_one_check()
            await asyncio.sleep(self.check_interval_seconds)

    async def run_one_check(self) -> None:
        """Run exactly one due-check and record the outcome in ``status``.

        Never raises — a bad check (no connected account yet, a transient
        Gmail/Sheets error) is logged and recorded so the loop keeps running
        for the next tick, the same "log failures, keep going" contract
        ``RealTimePoller`` applies to its own cycles.
        """
        from app.digest.service import generate_if_due

        self.status.check_count += 1
        self.status.last_check_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            outcome = await asyncio.to_thread(generate_if_due)
        except NotConnectedError:
            self.status.last_result = "not_connected"
            self.status.last_error = None
            return
        except Exception as exc:  # noqa: BLE001 — the loop must survive one bad check
            self.status.last_result = "error"
            self.status.last_error = str(exc)
            log.warning("digest_check_errored", extra={"error": str(exc)})
            return

        self.status.last_error = None
        self.status.last_result = outcome.result
        self.status.last_digest_date = outcome.digest_date


__all__ = ("DIGEST_CHECK_INTERVAL_SECONDS", "DigestScheduler", "DigestStatus")
