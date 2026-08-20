"""Near-real-time processing.

* :mod:`app.scheduling.history` — Gmail change detection via the history API
  (polling, not push — see the module docstring for why).
* :mod:`app.scheduling.retry` — transient-failure retry with backoff, shared
  by the history scan and the write path.
* :mod:`app.scheduling.poller` — :func:`run_poll_cycle`, the one real
  implementation of "find new mail, classify it with thread context, apply
  if the gate allows it." Called by ``POST /realtime/poll``.
* :mod:`app.scheduling.service` — :class:`RealTimePoller`, which tracks the
  outcome of each poll for ``GET /realtime/status``. There is no background
  loop; something outside this process calls ``/realtime/poll`` on a timer
  (a cron job, a scheduled HTTP ping) — see that module's docstring for why.
* :mod:`app.scheduling.state` — local file holding the Gmail history cursor
  between calls.
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
