"""The background-task wrapper for a historical run (Phase 15) — must run
its worker exactly once per start(), refuse a second concurrent start, and
let request_cancel() flip the cooperative flag the worker checks.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.historical.runner import HistoricalRunner


async def test_start_runs_the_worker_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def _fake_run(status, **kwargs):
        calls.append(kwargs)
        status.state = "completed"

    monkeypatch.setattr("app.historical.service.run_historical_cleanup", _fake_run)
    runner = HistoricalRunner()

    started = runner.start(months=6, confirm=True)
    assert started is True
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    assert calls[0]["months"] == 6
    assert calls[0]["confirm"] is True
    assert runner.status.state == "completed"


async def test_start_refuses_a_second_concurrent_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_historical_cleanup runs in a real OS thread via asyncio.to_thread,
    # so a plain threading.Event (not asyncio.Event) is what blocks it.
    release = threading.Event()

    def _fake_run(status, **kwargs):
        release.wait(timeout=5)

    monkeypatch.setattr("app.historical.service.run_historical_cleanup", _fake_run)
    runner = HistoricalRunner()

    assert runner.start() is True
    await asyncio.sleep(0.05)
    assert runner.start() is False  # refused — one is already running

    release.set()
    await asyncio.sleep(0.05)


async def test_request_cancel_sets_the_flag_the_worker_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_cancel: list[bool] = []

    def _fake_run(status, *, should_cancel, **kwargs):
        seen_cancel.append(should_cancel())
        status.state = "completed"

    monkeypatch.setattr("app.historical.service.run_historical_cleanup", _fake_run)
    runner = HistoricalRunner()
    runner.start()
    await asyncio.sleep(0.05)

    assert seen_cancel == [False]  # not cancelled during this quick run


async def test_request_cancel_returns_false_when_nothing_is_running() -> None:
    runner = HistoricalRunner()
    assert runner.request_cancel() is False


async def test_a_failed_worker_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(status, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.historical.service.run_historical_cleanup", _raise)
    runner = HistoricalRunner()
    runner.start()
    await asyncio.sleep(0.05)

    assert runner.status.state == "failed"
    assert "boom" in (runner.status.last_error or "")


async def test_status_resets_on_a_fresh_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(status, **kwargs):
        status.messages_processed = 42
        status.state = "completed"

    monkeypatch.setattr("app.historical.service.run_historical_cleanup", _fake_run)
    runner = HistoricalRunner()

    runner.start()
    await asyncio.sleep(0.05)
    assert runner.status.messages_processed == 42

    runner.start()
    await asyncio.sleep(0.05)
    assert runner.status.messages_processed == 42  # fake sets it again, but status object is fresh
    assert runner.status.cancel_requested is False
