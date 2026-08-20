"""Tracks the outcome of each on-demand poll cycle.

There is no in-process background loop anymore — that used to mean an
asyncio task ticking every ``REALTIME_POLL_INTERVAL_SECONDS`` forever, which
only works while the process itself stays resident. On a host that sleeps
after a period of no traffic (e.g. Render's free plan), a loop like that
just stops running along with the rest of the process, silently.

Instead, something *outside* this process — a cron job, a scheduled HTTP
ping, Windows Task Scheduler — is expected to call ``POST /realtime/poll``
on a timer. That request is what wakes a sleeping host back up, so the
"loop" and the "keep the host awake" problem solve each other instead of
fighting. This module just remembers what happened the last few times that
endpoint was hit, so ``GET /realtime/status`` has something honest to show.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class RealTimeStatus:
    """A snapshot for ``GET /realtime/status`` — never anything more than
    what's already safe to show: no message content, just counts and a
    result label."""

    poll_count: int = 0
    last_run_at: str | None = None
    #: "ok" | "bootstrapped" | "not_connected" | "error"
    last_result: str | None = None
    last_error: str | None = None
    last_messages_processed: int = 0
    last_changed_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "poll_count": self.poll_count,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "last_messages_processed": self.last_messages_processed,
            "last_changed_count": self.last_changed_count,
        }


class RealTimePoller:
    """Owns the status snapshot. One instance lives on the FastAPI app
    (``app.state.realtime_poller``) for the process's lifetime — which, on a
    host that sleeps, may only be a few minutes at a time. That's fine:
    there's nothing here that needs to survive a restart, and the real,
    durable state (the Gmail history cursor) already lives in
    :mod:`app.scheduling.state`, not here.
    """

    def __init__(self) -> None:
        self.status = RealTimeStatus()

    async def run_one_cycle(self, use_ai: bool = True):
        """Run exactly one poll cycle and record the outcome in ``status``.

        Unlike the old background-loop version, this does **not** swallow
        the exception — the caller (the ``/realtime/poll`` route) needs to
        know whether the call actually succeeded, so it can return the right
        HTTP status to whatever's calling it on a schedule. The outcome is
        still recorded first, so ``GET /realtime/status`` reflects it even
        if the caller doesn't inspect the exception itself.
        """
        from app.scheduling.poller import run_poll_cycle

        self.status.poll_count += 1
        self.status.last_run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            report = await asyncio.to_thread(run_poll_cycle, use_ai=use_ai)
        except Exception as exc:
            from app.google_api import NotConnectedError

            if isinstance(exc, NotConnectedError):
                self.status.last_result = "not_connected"
                self.status.last_error = None
            else:
                self.status.last_result = "error"
                self.status.last_error = str(exc)
            raise

        self.status.last_error = None
        self.status.last_result = "bootstrapped" if report.bootstrapped else "ok"
        self.status.last_messages_processed = report.messages_processed
        self.status.last_changed_count = report.changed_count
        return report


__all__ = ("RealTimePoller", "RealTimeStatus")
