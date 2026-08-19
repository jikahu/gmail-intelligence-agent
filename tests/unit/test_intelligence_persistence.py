"""Persisting intelligence to the workbook — idempotency above all (Phase 6)."""

from __future__ import annotations

from datetime import date

import pytest

from app.intelligence import analyze, persistence
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import make_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService

TODAY = date(2026, 8, 17)


@pytest.fixture
def workbook() -> ControlWorkbook:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    return ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)


def _report():
    messages = [
        make_message(
            sender="billing@netflix.com",
            sender_name="Netflix",
            subject="Your subscription renews soon",
            body="$15.99 per month, renews on September 1, 2026.",
            message_id="s1",
            thread_id="t1",
        ),
        make_message(
            sender="statements@chase.com",
            subject="Your statement is ready",
            body="Amount due $50.00, payment due by September 20, 2026.",
            message_id="d1",
            thread_id="t2",
        ),
        make_message(
            sender="itinerary@united.com",
            sender_name="United",
            subject="Your trip to Boston is booked",
            body="Flight to Boston on September 10, 2026. Confirmation ABC12.",
            message_id="tr1",
            thread_id="t3",
        ),
    ]
    return analyze(messages, today=TODAY)


def test_persist_populates_all_three_tabs(workbook: ControlWorkbook) -> None:
    counts = persistence.persist(workbook, _report(), today=TODAY)

    assert len(workbook.deadlines.all()) >= 1
    assert len(workbook.subscriptions.all()) >= 1
    assert len(workbook.trips.all()) >= 1
    assert counts["deadlines"]["errors"] == 0
    assert counts["subscriptions"]["errors"] == 0
    assert counts["trips"]["errors"] == 0


def test_persist_is_idempotent(workbook: ControlWorkbook) -> None:
    report = _report()
    persistence.persist(workbook, report, today=TODAY)
    d1 = len(workbook.deadlines.all())
    s1 = len(workbook.subscriptions.all())
    t1 = len(workbook.trips.all())

    # A second identical run must update rows, not duplicate them.
    counts = persistence.persist(workbook, report, today=TODAY)

    assert len(workbook.deadlines.all()) == d1
    assert len(workbook.subscriptions.all()) == s1
    assert len(workbook.trips.all()) == t1
    assert counts["subscriptions"]["inserted"] == 0
    assert counts["subscriptions"]["updated"] >= 1


def test_persisted_deadline_row_carries_the_normalized_date(workbook: ControlWorkbook) -> None:
    persistence.persist(workbook, _report(), today=TODAY)
    rows = workbook.deadlines.all()
    dates_written = {row.get("normalized_date") for row in rows}
    assert "2026-09-20" in dates_written


def test_persisted_subscription_row_has_last_seen(workbook: ControlWorkbook) -> None:
    persistence.persist(workbook, _report(), today=TODAY)
    rows = workbook.subscriptions.all()
    assert any(row.get("last_seen") == "2026-08-17" for row in rows)


def test_no_full_account_number_is_ever_written(workbook: ControlWorkbook) -> None:
    # A statement with a full number in the body must not leak into any cell.
    messages = [
        make_message(
            sender="statements@chase.com",
            subject="Your statement is ready",
            body="balance $10. Account number 1234 5678 9012 3456. Due September 1, 2026.",
            message_id="d1",
            thread_id="t1",
        )
    ]
    report = analyze(messages, today=TODAY)
    persistence.persist(workbook, report, today=TODAY)

    grid = workbook.deadlines.all()
    for row in grid:
        for value in row.values.values():
            assert "9012" not in value  # the middle of the card number
            assert "5678" not in value
