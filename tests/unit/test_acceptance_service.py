"""The stratified acceptance run service (Phase 10, CLAUDE.md §15)."""

from __future__ import annotations

import pytest

from app.acceptance import service as acceptance_service
from app.acceptance.models import Stratum
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, gmail_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    acceptance_service.reset_cache()
    yield
    acceptance_service.reset_cache()


class FakeGmailClient:
    def __init__(self, by_query: dict | None = None, pool: list[dict] | None = None) -> None:
        self._by_query = by_query
        self._pool = pool
        self.calls: list[tuple[int, str | None]] = []

    def get_profile(self) -> dict:
        return {"emailAddress": DEFAULT_USER}

    def list_recent_messages(self, max_results: int = 10, query: str | None = None):
        self.calls.append((max_results, query))
        if self._by_query is not None:
            return self._by_query.get(query, [])[:max_results]
        return (self._pool or [])[:max_results]


def _msg(mid: str) -> dict:
    return gmail_message(
        message_id=mid, headers={"From": "a@b.com", "To": DEFAULT_USER, "Subject": "hi"}
    )


# --------------------------------------------------------------------
# build_stratified_sample
# --------------------------------------------------------------------


def test_build_stratified_sample_deduplicates_overlapping_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strata = (Stratum("a", "query-a", target=3), Stratum("b", "query-b", target=3))
    monkeypatch.setattr(acceptance_service, "STRATA", strata)
    monkeypatch.setattr(acceptance_service, "DEFAULT_SAMPLE_TARGET", 6)

    gmail = FakeGmailClient(
        by_query={
            "query-a": [_msg("m1"), _msg("m2"), _msg("m3")],
            "query-b": [_msg("m3"), _msg("m4"), _msg("m5")],
        }
    )

    sample, achieved = acceptance_service.build_stratified_sample(gmail, target_total=6)

    assert [m["id"] for m in sample] == ["m1", "m2", "m3", "m4", "m5"]
    assert achieved == {"a": 3, "b": 2}


def test_build_stratified_sample_tops_up_when_strata_come_up_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strata = (Stratum("a", "query-a", target=2),)
    monkeypatch.setattr(acceptance_service, "STRATA", strata)
    monkeypatch.setattr(acceptance_service, "DEFAULT_SAMPLE_TARGET", 5)

    gmail = FakeGmailClient(
        by_query={
            "query-a": [_msg("m1")],
            None: [_msg("m1"), _msg("m2"), _msg("m3"), _msg("m4")],
        }
    )

    sample, achieved = acceptance_service.build_stratified_sample(gmail, target_total=5)

    assert achieved["a"] == 1
    assert achieved["top_up"] == 3
    assert len(sample) == 4


def test_build_stratified_sample_scales_targets_for_a_smaller_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strata = (Stratum("a", "query-a", target=10), Stratum("b", "query-b", target=10))
    monkeypatch.setattr(acceptance_service, "STRATA", strata)
    monkeypatch.setattr(acceptance_service, "DEFAULT_SAMPLE_TARGET", 20)

    gmail = FakeGmailClient(
        by_query={
            "query-a": [_msg(f"a{i}") for i in range(10)],
            "query-b": [_msg(f"b{i}") for i in range(10)],
        }
    )

    acceptance_service.build_stratified_sample(gmail, target_total=10)

    asked = {query: max_results for max_results, query in gmail.calls}
    assert asked["query-a"] == 5
    assert asked["query-b"] == 5


# --------------------------------------------------------------------
# run_acceptance_test — end to end with real classification
# --------------------------------------------------------------------


def _pool_messages() -> list[dict]:
    return [
        gmail_message(
            message_id="p1",
            headers={
                "From": "alerts@bank.com",
                "To": DEFAULT_USER,
                "Subject": "Action needed: your payment failed",
            },
            plain_body="Your payment failed and your card was declined.",
        ),
        gmail_message(
            message_id="promo",
            headers={
                "From": "deals@shop.example",
                "To": DEFAULT_USER,
                "Subject": "50% off everything!",
                **bulk_headers(),
            },
            plain_body="Huge sale. Unsubscribe here.",
        ),
        gmail_message(
            message_id="work1",
            headers={"From": "colleague@company.example", "To": DEFAULT_USER, "Subject": "Project update"},
            plain_body="Sharing the latest status.",
        ),
    ]


@pytest.fixture
def fake_gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmailClient:
    client = FakeGmailClient(pool=_pool_messages())
    monkeypatch.setattr("app.gmail.client.get_client", lambda: client)
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: (_ for _ in ()).throw(
            RuntimeError("workbook unavailable")
        )),
    )
    return client


def test_run_acceptance_test_classifies_the_sample(fake_gmail: FakeGmailClient) -> None:
    report, results = acceptance_service.run_acceptance_test(
        target_total=30, use_ai=False, read_attachments=False
    )

    assert report.sample_size == len(results) == 3
    assert report.target_size == 30
    assert report.summary["total"] == 3
    # The engine's own safety invariant makes this structurally impossible.
    assert report.false_reviews == []
    assert report.passed is True
    assert any(row.sender_email == "deals@shop.example" for row in report.review_rows)


def test_run_acceptance_test_caches_the_report(fake_gmail: FakeGmailClient) -> None:
    report, _results = acceptance_service.run_acceptance_test(
        target_total=10, use_ai=False, read_attachments=False
    )

    assert acceptance_service.get_report(report.run_id) is report
    assert acceptance_service.latest_report() is report


def test_report_cache_evicts_the_oldest_beyond_five(fake_gmail: FakeGmailClient) -> None:
    run_ids = []
    for _ in range(6):
        report, _results = acceptance_service.run_acceptance_test(
            target_total=10, use_ai=False, read_attachments=False
        )
        run_ids.append(report.run_id)

    assert acceptance_service.get_report(run_ids[0]) is None
    assert acceptance_service.get_report(run_ids[-1]) is not None


# --------------------------------------------------------------------
# persist_report
# --------------------------------------------------------------------


@pytest.fixture
def workbook() -> ControlWorkbook:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    return ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)


def test_persist_report_writes_system_runs_audit_log_and_settings(
    fake_gmail: FakeGmailClient, workbook: ControlWorkbook
) -> None:
    report, results = acceptance_service.run_acceptance_test(
        target_total=10, use_ai=False, read_attachments=False
    )

    acceptance_service.persist_report(
        workbook, report, results, started_at="2026-01-01T00:00:00+00:00"
    )

    run_row = workbook.system_runs.for_run(report.run_id)
    assert run_row is not None
    assert run_row.get("emails_processed") == str(report.sample_size)
    assert run_row.get("mode") == "dry_run"
    assert run_row.get("undo_available") == "false"

    audit_rows = workbook.audit_log.for_run(report.run_id)
    assert len(audit_rows) == report.sample_size

    assert workbook.settings.get("last_acceptance_run_id") == report.run_id
    assert workbook.settings.get("last_acceptance_passed") == "true"
    assert workbook.settings.get("last_acceptance_at")
