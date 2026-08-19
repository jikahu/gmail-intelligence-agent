"""The always-on side of Phase 13: a background loop that calls
:func:`app.scheduling.poller.run_poll_cycle` on a timer.

Started from FastAPI's startup event, and only when
``settings.realtime_enabled`` is true — off by default, the same
conservative-until-opted-in pattern as ``dry_run`` / ``gmail_processing_enabled``.
Turning this on starts the *loop*; it does not by itself allow Gmail writes —
``check_write_gate`` still applies to every write a cycle attempts, exactly
like every other write path in this app. A user can also trigger a single
cycle by hand at any time via ``POST /realtime/poll``, whether or not this
loop is running; :func:`app.scheduling.poller.run_poll_cycle` is the one
implementation both paths share.

Gmail and Sheets calls are synchronous (``googleapiclient`` has no asyncio
support), so each cycle runs in a worker thread via ``asyncio.to_thread``
rather than blocking the event loop that serves the dashboard and every other
route for however long a poll takes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from app.google_api import NotConnectedError
from app.logging_config import get_logger

log = get_logger("app.scheduling.service")


@dataclass
class RealTimeStatus:
    """A snapshot for ``GET /realtime/status`` — never anything more than
    what's already safe to show: no message content, just counts and a
    result label."""

    running: bool = False
    poll_count: int = 0
    last_run_at: str | None = None
    #: "ok" | "bootstrapped" | "not_connected" | "error"
    last_result: str | None = None
    last_error: str | None = None
    last_messages_processed: int = 0
    last_changed_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "poll_count": self.poll_count,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "last_messages_processed": self.last_messages_processed,
            "last_changed_count": self.last_changed_count,
        }


class RealTimePoller:
    """Owns the background asyncio task. One instance lives on the FastAPI
    app (``app.state.realtime_poller``) for the process's lifetime."""

    def __init__(self, interval_seconds: int) -> None:
        self.interval_seconds = interval_seconds
        self.status = RealTimeStatus()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self.status.running = True
        self._task = asyncio.create_task(self._loop(), name="realtime-poller")
        log.info("realtime_poller_started", extra={"interval_seconds": self.interval_seconds})

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
        log.info("realtime_poller_stopped")

    async def _loop(self) -> None:
        while True:
            await self.run_one_cycle()
            await asyncio.sleep(self.interval_seconds)

    async def run_one_cycle(self) -> None:
        """Run exactly one poll cycle and record the outcome in ``status``.

        Never raises — a bad cycle (no connected account yet, a transient
        Gmail/Sheets error that outlasted its own retries) is logged and
        recorded in ``status`` so the loop keeps running for the next tick,
        the same "log failures, keep going" contract the poll cycle itself
        applies to individual messages.
        """
        from app.scheduling.poller import run_poll_cycle

        self.status.poll_count += 1
        self.status.last_run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            report = await asyncio.to_thread(run_poll_cycle)
        except NotConnectedError:
            self.status.last_result = "not_connected"
            self.status.last_error = None
            return
        except Exception as exc:  # noqa: BLE001 — the loop must survive one bad cycle
            self.status.last_result = "error"
            self.status.last_error = str(exc)
            log.warning("realtime_poll_cycle_errored", extra={"error": str(exc)})
            return

        self.status.last_error = None
        self.status.last_result = "bootstrapped" if report.bootstrapped else "ok"
        self.status.last_messages_processed = report.messages_processed
        self.status.last_changed_count = report.changed_count


__all__ = ("RealTimePoller", "RealTimeStatus")
