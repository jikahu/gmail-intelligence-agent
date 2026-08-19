"""The digest builder (Phase 14) — reorders/narrows the Command Center's own
data (Phase 8) into CLAUDE.md §13's digest section order, and the clock-aware
``generate_if_due`` check the background scheduler relies on.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.classification.engine import classify
from app.classification.pipeline import PreviewResult
from app.digest import service
from tests.fixtures.emails import bulk_headers, make_message

# A Monday, so "tomorrow" is a business day and lands in Due Soon.
TODAY = date(2026, 8, 17)


def _result(message) -> PreviewResult:
    return PreviewResult(message=message, classification=classify(message))


def _sample_results() -> list[PreviewResult]:
    return [
        _result(
            make_message(
                message_id="p1",
                sender="alerts@bank.com",
                sender_name="Example Bank",
                subject="Action needed: your payment failed",
                body="Your payment failed and your card was declined. Update it.",
            )
        ),
        _result(
            make_message(
                message_id="promo",
                sender="deals@shop.example",
                subject="50% off everything — limited time!",
                body="Huge sale, don't miss out. Unsubscribe here.",
                headers=bulk_headers(),
            )
        ),
        _result(
            make_message(
                message_id="late",
                sender="billing@utility.com",
                subject="Overdue notice",
                body="Payment due January 1, 2020. Please pay now.",
            )
        ),
    ]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> list[PreviewResult]:
    results = _sample_results()
    monkeypatch.setattr(
        "app.classification.pipeline.preview_recent", lambda **kwargs: results
    )
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(
            lambda cls, spreadsheet_id=None: (_ for _ in ()).throw(
                RuntimeError("workbook unavailable")
            )
        ),
    )
    return results


# --------------------------------------------------------------------
# build_digest
# --------------------------------------------------------------------


def test_sections_are_in_claude_md_digest_order(wired) -> None:
    report = service.build_digest(today=TODAY)
    assert [s.key for s in report.sections] == [
        "p1", "p2", "action", "overdue", "waiting", "due_soon", "review",
    ]


def test_digest_excludes_vip_and_subscription_sections(wired) -> None:
    report = service.build_digest(today=TODAY)
    keys = {s.key for s in report.sections}
    assert "vip" not in keys
    assert "subscriptions" not in keys


def test_digest_date_matches_the_requested_date(wired) -> None:
    report = service.build_digest(today=TODAY)
    assert report.digest_date == TODAY


def test_p1_and_review_sections_are_populated(wired) -> None:
    report = service.build_digest(today=TODAY)
    assert report.section("p1").count >= 1
    assert report.section("review").count >= 1


def test_section_rows_carry_sender_subject_and_reason(wired) -> None:
    report = service.build_digest(today=TODAY)
    row = report.section("p1").rows[0]
    assert row.sender_email == "alerts@bank.com"
    assert row.subject
    assert row.reason


def test_total_matches_sum_of_section_counts(wired) -> None:
    report = service.build_digest(today=TODAY)
    assert report.total == sum(s.count for s in report.sections)


def test_dry_run_flag_is_carried(wired) -> None:
    report = service.build_digest(today=TODAY)
    assert report.dry_run is True


def test_build_digest_defaults_today_to_the_configured_timezone(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    tz = ZoneInfo("America/New_York")

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: A002 — matching datetime.now's own signature
            return datetime(2026, 1, 2, 3, 0, tzinfo=tz)

    monkeypatch.setattr("app.digest.service.datetime", _FixedDateTime)
    report = service.build_digest(tz=tz)
    assert report.digest_date == date(2026, 1, 2)


def test_report_as_dict_round_trips_counts(wired) -> None:
    report = service.build_digest(today=TODAY)
    payload = service.report_as_dict(report)
    assert payload["digest_date"] == "2026-08-17"
    assert payload["counts"]["p1"] == report.section("p1").count
    assert payload["total"] == report.total
    assert len(payload["sections"]) == 7


# --------------------------------------------------------------------
# digest_timezone / digest_hour
# --------------------------------------------------------------------


class _FakeSettingsRepo:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str, default=None):
        return self._values.get(key, default)

    def get_int(self, key: str, default: int) -> int:
        raw = self._values.get(key)
        return int(raw) if raw is not None else default


class _FakeWorkbook:
    def __init__(self, values: dict[str, str]) -> None:
        self.settings = _FakeSettingsRepo(values)


def test_digest_timezone_prefers_the_workbook_value() -> None:
    wb = _FakeWorkbook({"digest_timezone": "Europe/London"})
    assert service.digest_timezone(wb) == ZoneInfo("Europe/London")


def test_digest_timezone_falls_back_to_env_when_workbook_has_none() -> None:
    wb = _FakeWorkbook({})
    assert service.digest_timezone(wb) == ZoneInfo("America/New_York")


def test_digest_timezone_falls_back_with_no_workbook() -> None:
    assert service.digest_timezone(None) == ZoneInfo("America/New_York")


def test_digest_hour_prefers_the_workbook_value() -> None:
    wb = _FakeWorkbook({"digest_hour": "6"})
    assert service.digest_hour(wb) == 6


def test_digest_hour_falls_back_to_env_default() -> None:
    assert service.digest_hour(_FakeWorkbook({})) == 0


# --------------------------------------------------------------------
# generate_if_due
# --------------------------------------------------------------------


class _FakeDigestLog:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def for_date(self, digest_date: str):
        return self.rows.get(digest_date)

    def record(self, **kwargs):
        action = "updated" if kwargs["digest_date"] in self.rows else "inserted"
        self.rows[kwargs["digest_date"]] = kwargs
        return action


class _DueFakeWorkbook:
    def __init__(self, hour: int, tz: str = "America/New_York") -> None:
        self.settings = _FakeSettingsRepo({"digest_timezone": tz, "digest_hour": str(hour)})
        self.digest_log = _FakeDigestLog()


def test_generate_if_due_reports_not_yet_due(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    wb = _DueFakeWorkbook(hour=23)  # unlikely to already be 23:00 in the fixed clock below
    tz = ZoneInfo("America/New_York")

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 17, 1, 0, tzinfo=tz)

    monkeypatch.setattr("app.digest.service.datetime", _FixedDateTime)
    outcome = service.generate_if_due(workbook=wb)
    assert outcome.result == "not_yet_due"
    assert wb.digest_log.rows == {}


def test_generate_if_due_builds_and_persists_once_due(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb = _DueFakeWorkbook(hour=0)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 17, 1, 0, tzinfo=tz)

    monkeypatch.setattr("app.digest.service.datetime", _FixedDateTime)
    outcome = service.generate_if_due(workbook=wb)
    assert outcome.result == "generated"
    assert outcome.digest_date == "2026-08-17"
    assert "2026-08-17" in wb.digest_log.rows


def test_generate_if_due_is_idempotent_the_same_day(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb = _DueFakeWorkbook(hour=0)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 17, 1, 0, tzinfo=tz)

    monkeypatch.setattr("app.digest.service.datetime", _FixedDateTime)
    first = service.generate_if_due(workbook=wb)
    second = service.generate_if_due(workbook=wb)
    assert first.result == "generated"
    assert second.result == "already_done"
    assert len(wb.digest_log.rows) == 1
