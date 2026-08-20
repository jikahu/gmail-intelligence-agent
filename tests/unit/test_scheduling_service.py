"""RealTimePoller — records the outcome of each on-demand poll cycle.

There is no background loop to test anymore (see app/scheduling/service.py's
docstring for why): something outside this process calls
``POST /realtime/poll`` on a timer instead, so these tests only cover
``run_one_cycle``'s bookkeeping.
"""

from __future__ import annotations

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
        "app.scheduling.poller.run_poll_cycle",
        lambda use_ai=True: _report(messages_processed=3, changed_count=2),
    )
    poller = RealTimePoller()

    report = await poller.run_one_cycle()

    assert report.changed_count == 2
    assert poller.status.poll_count == 1
    assert poller.status.last_result == "ok"
    assert poller.status.last_messages_processed == 3
    assert poller.status.last_changed_count == 2
    assert poller.status.last_error is None


async def test_run_one_cycle_reports_bootstrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.scheduling.poller.run_poll_cycle",
        lambda use_ai=True: _report(bootstrapped=True, messages_processed=0),
    )
    poller = RealTimePoller()

    await poller.run_one_cycle()

    assert poller.status.last_result == "bootstrapped"


async def test_run_one_cycle_records_not_connected_then_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(use_ai=True):
        raise NotConnectedError("no token")

    monkeypatch.setattr("app.scheduling.poller.run_poll_cycle", _raise)
    poller = RealTimePoller()

    with pytest.raises(NotConnectedError):
        await poller.run_one_cycle()

    assert poller.status.last_result == "not_connected"


async def test_run_one_cycle_records_an_unexpected_error_then_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(use_ai=True):
        raise RuntimeError("Gmail is briefly unavailable")

    monkeypatch.setattr("app.scheduling.poller.run_poll_cycle", _raise)
    poller = RealTimePoller()

    with pytest.raises(RuntimeError):
        await poller.run_one_cycle()

    assert poller.status.last_result == "error"
    assert "Gmail" in (poller.status.last_error or "")


async def test_poll_count_accumulates_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.scheduling.poller.run_poll_cycle", lambda use_ai=True: _report())
    poller = RealTimePoller()

    await poller.run_one_cycle()
    await poller.run_one_cycle()

    assert poller.status.poll_count == 2
