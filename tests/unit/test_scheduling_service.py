"""The background poll loop (Phase 13, CLAUDE.md §13) — must survive a bad
cycle and keep ticking, and must stop cleanly.
"""

from __future__ import annotations

import asyncio

import pytest

from app.google_api import NotConnectedError
from app.scheduling.poller import PollReport
from app.scheduling.service import RealTimePoller


def _report(**overrides) -> PollReport:
    base = dict(
        bootstrapped=False,
        history_gap=False,
        gate_allowed=True,
        gate_reasons=(),
        messages_seen=1,
        messages_processed=1,
        changed_count=1,
        error_count=0,
    )
    base.update(overrides)
    return PollReport(**base)


async def test_run_one_cycle_records_a_successful_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.scheduling.poller.run_poll_cycle", lambda: _report(messages_processed=3, changed_count=2)
    )
    poller = RealTimePoller(interval_seconds=60)

    await poller.run_one_cycle()

    assert poller.status.poll_count == 1
    assert poller.status.last_result == "ok"
    assert poller.status.last_messages_processed == 3
    assert poller.status.last_changed_count == 2
    assert poller.status.last_error is None


async def test_run_one_cycle_reports_bootstrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.scheduling.poller.run_poll_cycle", lambda: _report(bootstrapped=True, messages_processed=0)
    )
    poller = RealTimePoller(interval_seconds=60)

    await poller.run_one_cycle()

    assert poller.status.last_result == "bootstrapped"


async def test_run_one_cycle_handles_not_connected_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise():
        raise NotConnectedError("no token")

    monkeypatch.setattr("app.scheduling.poller.run_poll_cycle", _raise)
    poller = RealTimePoller(interval_seconds=60)

    await poller.run_one_cycle()  # must not raise

    assert poller.status.last_result == "not_connected"


async def test_run_one_cycle_survives_an_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise():
        raise RuntimeError("Sheets is briefly unavailable")

    monkeypatch.setattr("app.scheduling.poller.run_poll_cycle", _raise)
    poller = RealTimePoller(interval_seconds=60)

    await poller.run_one_cycle()  # must not raise — the loop has to keep going

    assert poller.status.last_result == "error"
    assert "Sheets" in (poller.status.last_error or "")


async def test_start_ticks_repeatedly_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _fake_run_poll_cycle():
        calls["n"] += 1
        return _report()

    monkeypatch.setattr("app.scheduling.poller.run_poll_cycle", _fake_run_poll_cycle)
    poller = RealTimePoller(interval_seconds=0)

    poller.start()
    assert poller.status.running is True
    # Let the loop tick a few times — interval is 0, so this is fast.
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    await poller.stop()

    assert poller.status.running is False
    assert calls["n"] >= 1


async def test_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.scheduling.poller.run_poll_cycle", lambda: _report())
    poller = RealTimePoller(interval_seconds=60)

    poller.start()
    first_task = poller._task
    poller.start()  # a second call must not replace the running task

    assert poller._task is first_task
    await poller.stop()


async def test_stop_without_start_is_a_no_op() -> None:
    poller = RealTimePoller(interval_seconds=60)
    await poller.stop()  # must not raise
    assert poller.status.running is False
