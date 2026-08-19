"""Transient-failure retry with backoff (Phase 13, CLAUDE.md §13)."""

from __future__ import annotations

import pytest
from googleapiclient.errors import HttpError

from app.scheduling.retry import call_with_retry, is_transient


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "error"


def _http_error(status: int) -> HttpError:
    return HttpError(_FakeResp(status), b"error body")


def test_transient_statuses_are_recognized() -> None:
    for status in (429, 500, 502, 503, 504):
        assert is_transient(_http_error(status)) is True


def test_non_transient_statuses_are_not_retried() -> None:
    for status in (400, 401, 403, 404):
        assert is_transient(_http_error(status)) is False


def test_connection_and_timeout_errors_are_transient() -> None:
    assert is_transient(ConnectionError("dropped")) is True
    assert is_transient(TimeoutError("slow")) is True


def test_a_plain_value_error_is_not_transient() -> None:
    assert is_transient(ValueError("bad input")) is False


def test_call_with_retry_returns_on_first_success() -> None:
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    result = call_with_retry(fn, sleep=lambda _s: None)
    assert result == "ok"
    assert len(calls) == 1


def test_call_with_retry_retries_a_transient_failure_then_succeeds() -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _http_error(503)
        return "recovered"

    result = call_with_retry(fn, attempts=5, base_delay_seconds=1.0, sleep=sleeps.append)

    assert result == "recovered"
    assert attempts["n"] == 3
    # Exponential backoff: 1s, then 2s between the three attempts.
    assert sleeps == [1.0, 2.0]


def test_call_with_retry_gives_up_after_exhausting_attempts() -> None:
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _http_error(500)

    with pytest.raises(HttpError):
        call_with_retry(fn, attempts=3, sleep=lambda _s: None)
    assert attempts["n"] == 3


def test_call_with_retry_never_retries_a_non_transient_failure() -> None:
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _http_error(404)

    with pytest.raises(HttpError):
        call_with_retry(fn, attempts=5, sleep=lambda _s: None)
    # Not retried at all — a 404 will never succeed on a second try.
    assert attempts["n"] == 1
