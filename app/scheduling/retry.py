"""Retry transient failures (CLAUDE.md §13: "retry transient failures, log
failures").

Only *transient* failures are retried — a rate limit, a momentary 5xx, a
dropped connection. Anything else (a 404 for a message that genuinely no
longer exists, a 403 permission error, a bad request) is a real answer from
Gmail and is returned to the caller immediately, unretried, so it can be
logged and the mailbox moves on to the next message rather than burning poll
cycles on something retrying can never fix.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from googleapiclient.errors import HttpError

from app.logging_config import get_logger

log = get_logger("app.scheduling.retry")

T = TypeVar("T")

#: Gmail's own transient statuses: rate limiting and momentary server trouble.
_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Network-level failures worth a retry — never a signal about the data itself.
_TRANSIENT_EXCEPTION_TYPES: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)


def is_transient(exc: Exception) -> bool:
    """Whether ``exc`` is worth retrying rather than giving up on immediately."""
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        return status in _TRANSIENT_HTTP_STATUSES
    return isinstance(exc, _TRANSIENT_EXCEPTION_TYPES)


def call_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    description: str = "gmail_api_call",
) -> T:
    """Call ``fn()``, retrying transient failures with exponential backoff.

    A non-transient exception (per :func:`is_transient`) is re-raised on the
    first attempt — retrying it would just waste cycles. Exhausting all
    attempts on transient failures re-raises the last exception; the caller
    is expected to log it and skip that one message rather than crash the
    whole poll cycle (see :mod:`app.scheduling.poller`).
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — re-raised below if not transient
            if not is_transient(exc):
                raise
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay_seconds * (2 ** (attempt - 1))
            log.warning(
                "transient_failure_retrying",
                extra={
                    "description": description,
                    "attempt": attempt,
                    "attempts": attempts,
                    "delay_seconds": delay,
                    "error": str(exc),
                },
            )
            sleep(delay)
    assert last_exc is not None  # loop always runs at least once
    raise last_exc


__all__ = ("call_with_retry", "is_transient")
