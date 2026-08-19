"""The background-task wrapper around :func:`app.historical.service.run_historical_cleanup`
(Phase 15).

A twelve-month sweep can genuinely take a long time against a large mailbox
— far longer than a single HTTP request should ever block for. This mirrors
Phase 13's ``RealTimePoller`` and Phase 14's ``DigestScheduler`` in shape
(an ``asyncio.Task`` running synchronous Gmail/Sheets work via
``asyncio.to_thread``), but differs in one important way: it runs **once**
per ``start()`` call rather than looping on a timer, since a historical
cleanup is a deliberate, occasional action (CLAUDE.md §13: "run separately
from real-time processing"), never something started automatically.

Only one run may be active at a time — a second ``start()`` while one is
already running is refused rather than queued or stacked, so there's never
any ambiguity about which run a ``/historical/status`` poll is describing.
"""

from __future__ import annotations

import asyncio

from app.historical.models import HistoricalRunStatus
from app.logging_config import get_logger

log = get_logger("app.historical.runner")


class HistoricalRunner:
    """Owns the one background task a historical run runs in.

    One instance lives on the FastAPI app (``app.state.historical_runner``)
    for the process's lifetime, the same as ``RealTimePoller``/``DigestScheduler``.
    """

    def __init__(self) -> None:
        self.status = HistoricalRunStatus()
        self._task: asyncio.Task | None = None
        self._cancel_requested = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, **kwargs: object) -> bool:
        """Start a new sweep. Returns ``False`` (and starts nothing) if one
        is already active."""
        if self.is_running:
            return False
        self._cancel_requested = False
        self.status = HistoricalRunStatus()
        self.status.state = "running"
        self._task = asyncio.create_task(self._run(**kwargs), name="historical-cleanup")
        log.info("historical_run_started", extra={k: v for k, v in kwargs.items()})
        return True

    def request_cancel(self) -> bool:
        """Ask the active run to stop at its next between-pages check.

        Returns ``False`` if nothing is running. Cooperative, not forceful —
        a run already mid-page finishes that page first (see the service
        module's own docstring on why a page is the unit of interruption).
        """
        if not self.is_running:
            return False
        self._cancel_requested = True
        self.status.cancel_requested = True
        return True

    async def _run(self, **kwargs: object) -> None:
        from app.historical.service import run_historical_cleanup

        try:
            await asyncio.to_thread(
                run_historical_cleanup,
                self.status,
                should_cancel=lambda: self._cancel_requested,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — belt-and-suspenders; the
            # worker already catches its own failures into status, but a
            # background task must never let an exception vanish silently.
            self.status.state = "failed"
            self.status.last_error = str(exc)
            log.error("historical_run_task_errored", extra={"error": str(exc)})


__all__ = ("HistoricalRunner",)
