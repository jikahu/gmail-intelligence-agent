"""The Phase 15 twelve-month historical sweep worker
(app/historical/service.py) — pagination, the write-gate/confirm shape,
per-message error isolation, safety-invariant abort, and cancellation.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.historical import service
from app.historical.models import HistoricalRunStatus
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, gmail_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


# --------------------------------------------------------------------
# twelve_months_ago / historical_query — pure date math
# --------------------------------------------------------------------


def test_twelve_months_ago_same_day_one_year_back() -> None:
    assert service.twelve_months_ago(date(2026, 8, 19)) == date(2025, 8, 19)


def test_twelve_months_ago_clamps_leap_day_in_a_non_leap_year() -> None:
    assert service.twelve_months_ago(date(2024, 2, 29), months=12) == date(2023, 2, 28)


def test_twelve_months_ago_handles_more_than_a_year() -> None:
    assert service.twelve_months_ago(date(2026, 1, 15), months=13) == date(2024, 12, 15)


def test_historical_query_builds_an_after_search() -> None:
    assert service.historical_query(months=12, today=date(2026, 8, 19)) == "after:2025/08/19"


# --------------------------------------------------------------------
# run_historical_cleanup
# --------------------------------------------------------------------


class FakeHistoricalGmailClient:
    """Read client stand-in with a paginated ``list_message_ids``."""

    def __init__(self, pages: list[dict], messages_by_id: dict[str, dict], fail_ids: set[str] | None = None) -> None:
        self._pages = list(pages)
        self._by_id = messages_by_id
        self._fail_ids = fail_ids or set()
        self.get_message_calls: list[str] = []
        self.list_calls: int = 0

    def get_profile(self) -> dict:
        return {"emailAddress": DEFAULT_USER}

    def list_message_ids(self, query: str | None = None, max_results: int = 100, page_token: str | None = None):
        self.list_calls += 1
        return self._pages.pop(0)

    def get_message(self, message_id: str, message_format: str = "full"):
        self.get_message_calls.append(message_id)
        if message_id in self._fail_ids:
            raise RuntimeError(f"simulated fetch failure for {message_id}")
        return self._by_id[message_id]


class FakeWriteClient:
    def __init__(self, labels_by_id: dict[str, list[str]]) -> None:
        self._labels_by_id = labels_by_id
        self.modify_calls: list[str] = []

    def ensure_labels(self, names: list[str]) -> dict[str, str]:
        return {name: name for name in names}

    def modify_message(self, message_id, *, add_label_ids=None, remove_label_ids=None):
        self.modify_calls.append(message_id)
        current = set(self._labels_by_id.get(message_id, []))
        current |= set(add_label_ids or [])
        current -= set(remove_label_ids or [])
        self._labels_by_id[message_id] = sorted(current)
        return {"id": message_id, "labelIds": self._labels_by_id[message_id]}


def _message(message_id: str, *, promo: bool = False) -> dict:
    if promo:
        return gmail_message(
            message_id=message_id,
            headers={
                "From": "Deals <deals@shop.example>",
                "To": DEFAULT_USER,
                "Subject": "50% off everything — limited time!",
                **bulk_headers(),
            },
            plain_body="Huge sale, don't miss out. Unsubscribe here.",
            labels=["INBOX"],
        )
    return gmail_message(
        message_id=message_id,
        headers={
            "From": "Bank <alerts@bank.com>",
            "To": DEFAULT_USER,
            "Subject": "Your monthly statement",
        },
        plain_body="Your statement is ready.",
        labels=["INBOX"],
    )


def _two_pages(promo_ids: set[str] = frozenset()) -> tuple[list[dict], dict[str, dict]]:
    ids = ["m1", "m2", "m3", "m4"]
    messages = {mid: _message(mid, promo=mid in promo_ids) for mid in ids}
    pages = [
        {
            "messages": [{"id": "m1", "threadId": "m1"}, {"id": "m2", "threadId": "m2"}],
            "nextPageToken": "p2",
            "resultSizeEstimate": 4,
        },
        {
            "messages": [{"id": "m3", "threadId": "m3"}, {"id": "m4", "threadId": "m4"}],
        },
    ]
    return pages, messages


@pytest.fixture
def workbook(monkeypatch: pytest.MonkeyPatch) -> ControlWorkbook:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    wb = ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: wb),
    )
    return wb


def _wire_gmail(monkeypatch: pytest.MonkeyPatch, pages, messages, fail_ids=None) -> FakeHistoricalGmailClient:
    gmail = FakeHistoricalGmailClient(pages, messages, fail_ids=fail_ids)
    monkeypatch.setattr("app.gmail.client.get_client", lambda: gmail)
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    return gmail


def test_preview_run_processes_all_pages_without_writing(
    monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook
) -> None:
    pages, messages = _two_pages(promo_ids={"m2"})
    gmail = _wire_gmail(monkeypatch, pages, messages)

    status = HistoricalRunStatus()
    service.run_historical_cleanup(status, months=12, confirm=False, page_pause_seconds=0)

    assert status.state == "completed"
    assert status.pages_processed == 2
    assert status.messages_seen == 4
    assert status.messages_processed == 4
    assert status.messages_changed == 0  # preview never writes
    assert status.gate_allowed is False  # DRY_RUN defaults true
    assert len(gmail.get_message_calls) == 4

    # Preview must never write per-message Audit_Log rows (cost discipline).
    assert workbook.audit_log.all() == []
    # But a System_Runs summary row is always recorded.
    run = workbook.system_runs.for_run(status.run_id)
    assert run is not None
    assert run.get("mode") == "historical"
    assert run.get("emails_changed") == "0"
    assert run.get("undo_available") == "false"


def test_confirmed_run_with_closed_gate_still_previews(
    monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook
) -> None:
    pages, messages = _two_pages()
    _wire_gmail(monkeypatch, pages, messages)

    status = HistoricalRunStatus()
    service.run_historical_cleanup(status, confirm=True, page_pause_seconds=0)

    assert status.gate_allowed is False
    assert status.messages_changed == 0
    assert workbook.audit_log.all() == []


def _open_gate(monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    get_settings.cache_clear()
    workbook.settings.set("last_acceptance_passed", "true")


def test_confirmed_run_with_open_gate_writes_only_changed_messages(
    monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook
) -> None:
    pages, messages = _two_pages(promo_ids={"m2"})
    _wire_gmail(monkeypatch, pages, messages)
    _open_gate(monkeypatch, workbook)

    labels_by_id = {mid: ["INBOX"] for mid in messages}
    write_client = FakeWriteClient(labels_by_id)
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: write_client)

    status = HistoricalRunStatus()
    service.run_historical_cleanup(status, confirm=True, page_pause_seconds=0)

    assert status.gate_allowed is True
    assert status.messages_changed >= 1  # at least the promo message gets AI/Review + archived
    assert write_client.modify_calls  # a real write happened

    audit_rows = workbook.audit_log.all()
    assert len(audit_rows) == status.messages_changed
    for row in audit_rows:
        assert row.get("run_id") == status.run_id
        assert row.get("reversible") == "true"

    run = workbook.system_runs.for_run(status.run_id)
    assert run.get("undo_available") == "true"


def test_message_fetch_failure_is_isolated_not_fatal(
    monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook
) -> None:
    pages, messages = _two_pages()
    _wire_gmail(monkeypatch, pages, messages, fail_ids={"m2"})

    status = HistoricalRunStatus()
    service.run_historical_cleanup(status, confirm=False, page_pause_seconds=0)

    assert status.state == "completed"
    assert status.messages_seen == 4
    assert status.messages_processed == 3  # m2's fetch failed
    assert status.errors == 1
    assert "m2" in (status.last_error or "") or status.last_error is not None


def test_max_messages_stops_mid_page(
    monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook
) -> None:
    pages, messages = _two_pages()
    gmail = _wire_gmail(monkeypatch, pages, messages)

    status = HistoricalRunStatus()
    service.run_historical_cleanup(status, confirm=False, max_messages=1, page_pause_seconds=0)

    assert status.messages_seen == 1
    assert status.messages_processed == 1
    assert status.pages_processed == 1
    assert gmail.list_calls == 1  # never fetched the second page


def test_cancellation_stops_before_the_next_page(
    monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook
) -> None:
    pages, messages = _two_pages()
    gmail = _wire_gmail(monkeypatch, pages, messages)

    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # let the first page through, then cancel

    status = HistoricalRunStatus()
    service.run_historical_cleanup(
        status, confirm=False, page_pause_seconds=0, should_cancel=should_cancel
    )

    assert status.state == "cancelled"
    assert status.pages_processed == 1
    assert status.messages_seen == 2
    assert gmail.list_calls == 1


def test_safety_invariant_violation_aborts_the_whole_run(
    monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook
) -> None:
    pages, messages = _two_pages()
    _wire_gmail(monkeypatch, pages, messages)

    def _raise(*args, **kwargs):
        raise AssertionError("a protected email was nearly routed to Review")

    monkeypatch.setattr("app.classification.pipeline.classify_raw_messages", _raise)

    status = HistoricalRunStatus()
    service.run_historical_cleanup(status, confirm=False, page_pause_seconds=0)

    assert status.state == "failed"
    assert "safety invariant" in (status.last_error or "")
    # The run still gets a System_Runs row so the failed attempt is on record.
    run = workbook.system_runs.for_run(status.run_id)
    assert run is not None


def test_not_connected_sets_state_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.google_api import NotConnectedError

    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: (_ for _ in ()).throw(
            NotConnectedError("no token")
        )),
    )

    status = HistoricalRunStatus()
    service.run_historical_cleanup(status, confirm=False)  # must not raise

    assert status.state == "not_connected"
