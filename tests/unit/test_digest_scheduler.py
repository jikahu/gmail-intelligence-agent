"""The background digest scheduler (Phase 14) — must survive a bad check and
keep ticking, and must stop cleanly. Mirrors test_scheduling_service.py.
"""

from __future__ import annotations

import asyncio

import pytest

from app.digest.scheduler import DigestScheduler
from app.digest.service import DigestCheckOutcome
from app.google_api import NotConnectedError


async def test_run_one_check_records_a_generated_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.digest.service.generate_if_due",
        lambda: DigestCheckOutcome(result="generated", digest_date="2026-08-17"),
    )
    scheduler = DigestScheduler(check_interval_seconds=60)

    await scheduler.run_one_check()

    assert scheduler.status.check_count == 1
    assert scheduler.status.last_result == "generated"
    assert scheduler.status.last_digest_date == "2026-08-17"
    assert scheduler.status.last_error is None


async def test_run_one_check_records_not_yet_due(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.digest.service.generate_if_due",
        lambda: DigestCheckOutcome(result="not_yet_due", digest_date="2026-08-17"),
    )
    scheduler = DigestScheduler(check_interval_seconds=60)

    await scheduler.run_one_check()

    assert scheduler.status.last_result == "not_yet_due"


async def test_run_one_check_handles_not_connected_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise():
        raise NotConnectedError("no token")

    monkeypatch.setattr("app.digest.service.generate_if_due", _raise)
    scheduler = DigestScheduler(check_interval_seconds=60)

    await scheduler.run_one_check()  # must not raise

    assert scheduler.status.last_result == "not_connected"


async def test_run_one_check_survives_an_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise():
        raise RuntimeError("Sheets is briefly unavailable")

    monkeypatch.setattr("app.digest.service.generate_if_due", _raise)
    scheduler = DigestScheduler(check_interval_seconds=60)

    await scheduler.run_one_check()  # must not raise — the loop has to keep going

    assert scheduler.status.last_result == "error"
    assert "Sheets" in (scheduler.status.last_error or "")


async def test_start_ticks_repeatedly_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _fake_generate_if_due():
        calls["n"] += 1
        return DigestCheckOutcome(result="already_done", digest_date="2026-08-17")

    monkeypatch.setattr("app.digest.service.generate_if_due", _fake_generate_if_due)
    scheduler = DigestScheduler(check_interval_seconds=0)

    scheduler.start()
    assert scheduler.status.running is True
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    await scheduler.stop()

    assert scheduler.status.running is False
    assert calls["n"] >= 1


async def test_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.digest.service.generate_if_due",
        lambda: DigestCheckOutcome(result="already_done", digest_date="2026-08-17"),
    )
    scheduler = DigestScheduler(check_interval_seconds=60)

    scheduler.start()
    first_task = scheduler._task
    scheduler.start()  # a second call must not replace the running task

    assert scheduler._task is first_task
    await scheduler.stop()


async def test_stop_without_start_is_a_no_op() -> None:
    scheduler = DigestScheduler(check_interval_seconds=60)
    await scheduler.stop()  # must not raise
    assert scheduler.status.running is False
