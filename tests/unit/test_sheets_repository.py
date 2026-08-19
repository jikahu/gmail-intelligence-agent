"""Repository-layer tests — the interface the rest of the app codes against."""

from __future__ import annotations

import pytest

from app.sheets.repository import ControlWorkbook, SheetTable
from app.sheets.schema import SENDER_RULES_TAB, SETTINGS_TAB
from app.sheets.workbook import WORKBOOK_NAME, ensure_workbook
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


@pytest.fixture
def workbook() -> tuple[ControlWorkbook, FakeSheetsService]:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    return ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets), sheets


# --------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------


def test_settings_reads_the_seeded_defaults(workbook) -> None:
    wb, _sheets = workbook
    assert wb.settings.get("dry_run") == "true"
    assert wb.settings.get("digest_timezone") == "America/New_York"


def test_settings_typed_getters(workbook) -> None:
    wb, _sheets = workbook
    settings = wb.settings

    assert settings.get_bool("dry_run", default=False) is True
    assert settings.get_bool("gmail_processing_enabled", default=True) is False
    assert settings.get_int("digest_hour", default=9) == 0
    assert settings.get_float("review_confidence_threshold", default=0.1) == 0.7


def test_settings_typed_getters_fall_back_on_junk(workbook) -> None:
    wb, _sheets = workbook
    wb.settings.set("digest_hour", "not-a-number")
    wb.settings.set("review_confidence_threshold", "")

    assert wb.settings.get_int("digest_hour", default=3) == 3
    assert wb.settings.get_float("review_confidence_threshold", default=0.5) == 0.5
    assert wb.settings.get_bool("nonexistent_key", default=True) is True


def test_settings_accepts_human_boolean_spellings(workbook) -> None:
    wb, _sheets = workbook
    for written, expected in (("YES", True), ("no", False), ("On", True), ("0", False)):
        wb.settings.set("dry_run", written)
        assert wb.settings.get_bool("dry_run", default=None) is expected


def test_settings_set_updates_in_place(workbook) -> None:
    wb, sheets = workbook
    before = len(sheets.spreadsheets_by_id[wb.spreadsheet_id].read("'Settings'!A2:D"))

    wb.settings.set("dry_run", "false")

    after = sheets.spreadsheets_by_id[wb.spreadsheet_id].read("'Settings'!A2:D")
    assert len(after) == before  # Updated, not appended.
    assert wb.settings.get("dry_run") == "false"


def test_settings_set_stamps_updated_at(workbook) -> None:
    wb, _sheets = workbook
    wb.settings.set("dry_run", "false")

    row = wb.table(SETTINGS_TAB).first(key="dry_run")
    assert row is not None
    assert row.get("updated_at")


def test_settings_set_preserves_the_description(workbook) -> None:
    wb, _sheets = workbook
    original = wb.table(SETTINGS_TAB).first(key="dry_run").get("description")

    wb.settings.set("dry_run", "false")

    assert wb.table(SETTINGS_TAB).first(key="dry_run").get("description") == original


def test_settings_set_adds_an_unknown_key(workbook) -> None:
    wb, _sheets = workbook
    wb.settings.set("new_flag", "true", description="Added by a later phase.")

    assert wb.settings.get("new_flag") == "true"
    assert "new_flag" in wb.settings.all()


# --------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------


def test_sender_rules_filter_by_status_and_lowercase(workbook) -> None:
    wb, _sheets = workbook
    table = wb.table(SENDER_RULES_TAB)
    table.append(
        {
            "sender": "Alerts@MyBank.COM",
            "rule_type": "classify_as",
            "action": "AI/Financial",
            "status": "active",
            "source": "manual",
        }
    )
    table.append(
        {
            "sender": "old@example.com",
            "rule_type": "blacklist",
            "action": "",
            "status": "paused",
            "source": "manual",
        }
    )

    active = wb.rules.get_sender_rules()
    assert [r.sender for r in active] == ["alerts@mybank.com"]
    assert active[0].action == "AI/Financial"

    assert len(wb.rules.get_sender_rules(status=None)) == 2


def test_domain_rules_strip_the_at_sign(workbook) -> None:
    wb, _sheets = workbook
    wb.table("Domain_Rules").append(
        {
            "domain": "@Substack.com",
            "rule_type": "whitelist",
            "action": "AI/Newsletter",
            "status": "active",
            "source": "manual",
        }
    )

    assert [r.domain for r in wb.rules.get_domain_rules()] == ["substack.com"]


def test_add_rule_suggestion_is_pending_not_applied(workbook) -> None:
    wb, _sheets = workbook
    suggestion_id = wb.rules.add_rule_suggestion(
        target="news@example.com",
        suggested_rule="classify_as AI/Newsletter",
        evidence="User kept 4 of 4 messages from this sender.",
        confidence=0.82,
    )

    assert suggestion_id
    pending = wb.rules.pending_suggestions()
    assert len(pending) == 1
    assert pending[0].get("suggestion_id") == suggestion_id
    assert pending[0].get("status") == "pending"
    assert pending[0].get("confidence") == "0.82"
    assert pending[0].get("approved_at") == ""

    # A suggestion must never become an active rule on its own.
    assert wb.rules.get_sender_rules() == []


def test_add_sender_rule_creates_an_active_rule(workbook) -> None:
    wb, _sheets = workbook
    wb.rules.add_sender_rule("Friend@Example.com", notes="from the dashboard")

    active = wb.rules.get_sender_rules()
    assert [r.sender for r in active] == ["friend@example.com"]
    assert active[0].source == "learned"
    assert active[0].rule_type == "whitelist"


def test_add_sender_rule_is_idempotent_on_sender(workbook) -> None:
    wb, _sheets = workbook
    wb.rules.add_sender_rule("friend@example.com", notes="first")
    wb.rules.add_sender_rule("friend@example.com", notes="second")

    assert len(wb.rules.get_sender_rules()) == 1
    assert wb.rules.get_sender_rules()[0].notes == "second"


def test_add_domain_rule_strips_the_at_sign_and_lowercases(workbook) -> None:
    wb, _sheets = workbook
    wb.rules.add_domain_rule("@MyBank.COM")

    assert [r.domain for r in wb.rules.get_domain_rules()] == ["mybank.com"]


def test_approved_suggestions_only_returns_approved_status(workbook) -> None:
    wb, _sheets = workbook
    sid = wb.rules.add_rule_suggestion(
        target="a@example.com", suggested_rule="whitelist", evidence="x", confidence=1.0
    )
    assert wb.rules.approved_suggestions() == []

    table = wb.table("Learned_Rule_Suggestions")
    table.update(table.first(suggestion_id=sid), {"status": "approved"})

    approved = wb.rules.approved_suggestions()
    assert len(approved) == 1
    assert approved[0].get("suggestion_id") == sid


# --------------------------------------------------------------------
# Review feedback + audit log
# --------------------------------------------------------------------


def test_review_feedback_records_a_row_with_a_timestamp(workbook) -> None:
    wb, _sheets = workbook
    wb.review_feedback.record(
        gmail_message_id="m1",
        thread_id="t1",
        original_classification="AI/Review",
        original_reason="bulk mailing",
        user_decision="kept",
    )

    rows = wb.review_feedback.all()
    assert len(rows) == 1
    assert rows[0].get("gmail_message_id") == "m1"
    assert rows[0].get("timestamp")


def test_review_feedback_for_message_filters_correctly(workbook) -> None:
    wb, _sheets = workbook
    wb.review_feedback.record(
        gmail_message_id="m1", thread_id="t1", original_classification="AI/Review",
        original_reason="x", user_decision="kept",
    )
    wb.review_feedback.record(
        gmail_message_id="m2", thread_id="t2", original_classification="AI/Review",
        original_reason="x", user_decision="kept",
    )

    assert len(wb.review_feedback.for_message("m1")) == 1
    assert len(wb.review_feedback.for_message("m2")) == 1


def test_system_runs_record_and_for_run(workbook) -> None:
    wb, _sheets = workbook
    wb.system_runs.record(
        run_id="run-1",
        mode="dry_run",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:05:00+00:00",
        emails_processed=250,
    )

    row = wb.system_runs.for_run("run-1")
    assert row is not None
    assert row.get("emails_processed") == "250"
    assert row.get("mode") == "dry_run"
    assert row.get("undo_available") == "false"
    assert row.get("errors") == "0"


def test_system_runs_all_lists_every_row(workbook) -> None:
    wb, _sheets = workbook
    wb.system_runs.record(run_id="a", mode="dry_run", started_at="x", completed_at="y", emails_processed=1)
    wb.system_runs.record(run_id="b", mode="dry_run", started_at="x", completed_at="y", emails_processed=2)

    assert len(wb.system_runs.all()) == 2


def test_latest_undoable_returns_none_when_nothing_is_undoable(workbook) -> None:
    wb, _sheets = workbook
    wb.system_runs.record(
        run_id="a", mode="dry_run", started_at="x", completed_at="y",
        emails_processed=1, undo_available=False,
    )
    assert wb.system_runs.latest_undoable() is None


def test_latest_undoable_returns_the_most_recently_appended_match(workbook) -> None:
    wb, _sheets = workbook
    wb.system_runs.record(
        run_id="older", mode="live", started_at="x", completed_at="y",
        emails_processed=1, undo_available=True,
    )
    wb.system_runs.record(
        run_id="newer", mode="live", started_at="x", completed_at="y",
        emails_processed=1, undo_available=True,
    )
    row = wb.system_runs.latest_undoable()
    assert row is not None
    assert row.get("run_id") == "newer"


def test_mark_undone_flips_the_flag_and_is_then_excluded(workbook) -> None:
    wb, _sheets = workbook
    wb.system_runs.record(
        run_id="run-1", mode="live", started_at="x", completed_at="y",
        emails_processed=1, undo_available=True,
    )
    assert wb.system_runs.mark_undone("run-1") is True
    assert wb.system_runs.for_run("run-1").get("undo_available") == "false"
    assert wb.system_runs.latest_undoable() is None


def test_mark_undone_returns_false_for_an_unknown_run(workbook) -> None:
    wb, _sheets = workbook
    assert wb.system_runs.mark_undone("does-not-exist") is False


def test_audit_log_record_appends_and_for_run_filters(workbook) -> None:
    wb, _sheets = workbook
    wb.audit_log.record({"event_id": "e1", "run_id": "run-a", "gmail_message_id": "m1"})
    wb.audit_log.record({"event_id": "e2", "run_id": "run-b", "gmail_message_id": "m2"})

    assert len(wb.audit_log.all()) == 2
    run_a = wb.audit_log.for_run("run-a")
    assert len(run_a) == 1
    assert run_a[0].get("gmail_message_id") == "m1"


# --------------------------------------------------------------------
# Digest_Log
# --------------------------------------------------------------------


def _record_digest(wb, digest_date: str, **overrides) -> str:
    counts = {"p1": 1, "p2": 2, "action": 0, "overdue": 0, "waiting": 0, "due_soon": 0, "review": 3}
    counts.update(overrides.pop("counts", {}))
    return wb.digest_log.record(
        digest_date=digest_date,
        generated_at="2026-01-01T05:00:00+00:00",
        timezone="America/New_York",
        account="user@example.com",
        counts=counts,
        total=sum(counts.values()),
        **overrides,
    )


def test_digest_log_record_and_for_date(workbook) -> None:
    wb, _sheets = workbook
    action = _record_digest(wb, "2026-01-01")
    assert action == "inserted"

    row = wb.digest_log.for_date("2026-01-01")
    assert row is not None
    assert row.get("p1_count") == "1"
    assert row.get("review_count") == "3"
    assert row.get("total_count") == "6"
    assert row.get("account") == "user@example.com"


def test_digest_log_for_date_returns_none_when_missing(workbook) -> None:
    wb, _sheets = workbook
    assert wb.digest_log.for_date("2026-01-01") is None


def test_digest_log_record_is_idempotent_per_date(workbook) -> None:
    wb, _sheets = workbook
    first = _record_digest(wb, "2026-01-01", counts={"review": 3})
    second = _record_digest(wb, "2026-01-01", counts={"review": 9})

    assert first == "inserted"
    assert second == "updated"
    assert len(wb.digest_log.all()) == 1
    assert wb.digest_log.for_date("2026-01-01").get("review_count") == "9"


def test_digest_log_latest_returns_the_most_recent_date(workbook) -> None:
    wb, _sheets = workbook
    _record_digest(wb, "2026-01-01")
    _record_digest(wb, "2026-01-03")
    _record_digest(wb, "2026-01-02")

    latest = wb.digest_log.latest()
    assert latest is not None
    assert latest.get("digest_date") == "2026-01-03"


def test_digest_log_latest_returns_none_when_empty(workbook) -> None:
    wb, _sheets = workbook
    assert wb.digest_log.latest() is None


# --------------------------------------------------------------------
# VIPs
# --------------------------------------------------------------------


def test_vip_suggestion_is_not_approved(workbook) -> None:
    wb, _sheets = workbook
    wb.vips.suggest("boss@work.com", name="The Boss")

    assert wb.vips.approved_emails() == set()
    assert wb.vips.is_vip("boss@work.com") is False


def test_vip_approval_takes_effect(workbook) -> None:
    wb, _sheets = workbook
    table = wb.table("VIPs")
    table.append({"email": "boss@work.com", "name": "The Boss", "status": "approved"})

    assert wb.vips.is_vip("Boss@Work.com") is True
    assert wb.vips.approved_emails() == {"boss@work.com"}


def test_vip_suggest_does_not_duplicate(workbook) -> None:
    wb, _sheets = workbook
    wb.vips.suggest("boss@work.com")
    wb.vips.suggest("boss@work.com")

    assert len(wb.table("VIPs").rows()) == 1


# --------------------------------------------------------------------
# Sheets quirks the layer must absorb
# --------------------------------------------------------------------


def test_columns_are_addressed_by_name_not_position(workbook) -> None:
    """Reordering columns in the sheet must not change what the app reads."""
    wb, sheets = workbook
    book = sheets.spreadsheets_by_id[wb.spreadsheet_id]

    # Rewrite the Settings tab with the columns in a different order.
    book.grids["Settings"] = [
        ["description", "updated_at", "value", "key"],
        ["Master dry-run switch.", "2026-08-17T00:00:00+00:00", "false", "dry_run"],
    ]
    wb.invalidate_all()

    assert wb.settings.get("dry_run") == "false"
    assert wb.settings.get_bool("dry_run", default=True) is False


def test_short_rows_from_sheets_trimming_are_padded(workbook) -> None:
    """Sheets drops trailing empty cells; missing values must read as ""."""
    wb, sheets = workbook
    book = sheets.spreadsheets_by_id[wb.spreadsheet_id]
    book.grids["VIPs"] = [
        ["email", "name", "status", "approved_at", "notes"],
        ["someone@example.com"],  # Only one cell present.
    ]
    wb.invalidate_all()

    row = wb.table("VIPs").rows()[0]
    assert row.get("email") == "someone@example.com"
    assert row.get("notes") == ""
    assert row.get("status") == ""


def test_blank_spacer_rows_are_skipped(workbook) -> None:
    wb, sheets = workbook
    book = sheets.spreadsheets_by_id[wb.spreadsheet_id]
    book.grids["VIPs"] = [
        ["email", "name", "status", "approved_at", "notes"],
        ["a@example.com", "A", "approved", "", ""],
        ["", "", "", "", ""],
        ["b@example.com", "B", "approved", "", ""],
    ]
    wb.invalidate_all()

    rows = wb.table("VIPs").rows()
    assert [r.get("email") for r in rows] == ["a@example.com", "b@example.com"]


def test_row_numbers_point_at_the_real_sheet_row(workbook) -> None:
    wb, sheets = workbook
    book = sheets.spreadsheets_by_id[wb.spreadsheet_id]
    book.grids["VIPs"] = [
        ["email", "name", "status", "approved_at", "notes"],
        ["a@example.com", "A", "approved", "", ""],
        ["", "", "", "", ""],
        ["b@example.com", "B", "approved", "", ""],
    ]
    wb.invalidate_all()

    rows = wb.table("VIPs").rows()
    assert rows[0].number == 2
    assert rows[1].number == 4  # Row 3 was the blank spacer.


# --------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------


def test_repeat_reads_hit_the_cache(workbook) -> None:
    wb, sheets = workbook
    table = wb.table(SETTINGS_TAB)

    table.rows()
    calls_after_first = sheets.call_counts["values.get"]
    for _ in range(5):
        table.rows()

    assert sheets.call_counts["values.get"] == calls_after_first


def test_a_write_invalidates_the_cache(workbook) -> None:
    wb, sheets = workbook
    table = wb.table(SETTINGS_TAB)
    table.rows()
    before = sheets.call_counts["values.get"]

    wb.settings.set("dry_run", "false")
    table.rows()

    assert sheets.call_counts["values.get"] > before


def test_cache_expires(workbook, monkeypatch: pytest.MonkeyPatch) -> None:
    wb, sheets = workbook
    table = wb.table(SETTINGS_TAB)
    table.rows()
    before = sheets.call_counts["values.get"]

    clock = [1_000_000.0]
    monkeypatch.setattr("app.sheets.repository.time.monotonic", lambda: clock[0])
    table.invalidate()
    table.rows()
    mid = sheets.call_counts["values.get"]
    assert mid > before

    clock[0] += 3600
    table.rows()
    assert sheets.call_counts["values.get"] > mid


# --------------------------------------------------------------------
# Guard rails
# --------------------------------------------------------------------


def test_append_without_a_header_is_a_clear_error(workbook) -> None:
    wb, sheets = workbook
    book = sheets.spreadsheets_by_id[wb.spreadsheet_id]
    book.grids["VIPs"] = []
    wb.invalidate_all()

    with pytest.raises(RuntimeError, match="no header row"):
        wb.table("VIPs").append({"email": "x@y.com"})


def test_unknown_columns_are_dropped_not_invented(workbook) -> None:
    wb, _sheets = workbook
    table = wb.table("VIPs")
    table.append({"email": "x@y.com", "status": "approved", "not_a_column": "boom"})

    row = table.rows()[0]
    assert row.get("email") == "x@y.com"
    assert "not_a_column" not in row.values
    assert table.header() == list(wb.table("VIPs")._tab.column_names)


def test_update_only_touches_the_named_columns(workbook) -> None:
    wb, _sheets = workbook
    table = wb.table("VIPs")
    table.append(
        {
            "email": "x@y.com",
            "name": "Ex Why",
            "status": "pending",
            "notes": "keep me",
        }
    )
    row = table.rows()[0]

    table.update(row, {"status": "approved"})

    updated = table.rows()[0]
    assert updated.get("status") == "approved"
    assert updated.get("name") == "Ex Why"
    assert updated.get("notes") == "keep me"


def test_table_lookup_accepts_a_name_or_a_tab(workbook) -> None:
    wb, _sheets = workbook
    assert isinstance(wb.table("Settings"), SheetTable)
    assert wb.table("Settings") is wb.table(SETTINGS_TAB)

    with pytest.raises(KeyError):
        wb.table("No_Such_Tab")


def test_connect_is_wired_to_ensure_workbook(monkeypatch: pytest.MonkeyPatch) -> None:
    """ControlWorkbook.connect() must initialize the workbook before use."""
    sheets = FakeSheetsService()
    drive = FakeDriveService()

    monkeypatch.setattr("app.sheets.repository.get_sheets_service", lambda: sheets)
    monkeypatch.setattr("app.sheets.workbook.get_drive_service", lambda: drive)

    wb = ControlWorkbook.connect()

    assert wb.spreadsheet_id in sheets.spreadsheets_by_id
    assert wb.settings.get("dry_run") == "true"
    assert drive.queries  # It really did look in Drive first.
    assert sheets.spreadsheets_by_id[wb.spreadsheet_id].title == WORKBOOK_NAME
