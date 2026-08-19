"""Near-real-time processing (Phase 13, CLAUDE.md §13).

* :mod:`app.scheduling.history` — Gmail change detection via the history API
  (polling, not push — see the module docstring for why).
* :mod:`app.scheduling.retry` — transient-failure retry with backoff, shared
  by the history scan and the write path.
* :mod:`app.scheduling.poller` — :func:`run_poll_cycle`, the one real
  implementation of "find new mail, classify it with thread context, apply
  if the gate allows it." Callable on a timer or by hand.
* :mod:`app.scheduling.service` — :class:`RealTimePoller`, the background
  asyncio loop that calls ``run_poll_cycle`` every
  ``settings.realtime_poll_interval_seconds``, started only when
  ``settings.realtime_enabled`` is true.
"""

from app.scheduling.poller import PollReport, ProcessedMessage, run_poll_cycle
from app.scheduling.service import RealTimePoller, RealTimeStatus

__all__ = (
    "PollReport",
    "ProcessedMessage",
    "RealTimePoller",
    "RealTimeStatus",
    "run_poll_cycle",
)
