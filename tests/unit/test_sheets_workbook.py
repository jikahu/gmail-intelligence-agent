"""Workbook creation and schema-drift tests — no real Google calls."""

from __future__ import annotations

import pytest

from app.sheets.schema import DEFAULT_SETTINGS, WORKBOOK_TABS, all_tab_names
from app.sheets.workbook import (
    WORKBOOK_NAME,
    column_letter,
    ensure_workbook,
    find_workbook_id,
    quote_tab,
)
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


@pytest.fixture
def services() -> tuple[FakeSheetsService, FakeDriveService]:
    return FakeSheetsService(), FakeDriveService()


def _register_in_drive(drive: FakeDriveService, spreadsheet_id: str) -> None:
    """Mimic the workbook now being discoverable in Drive."""
    drive.files_present.append({"id": spreadsheet_id, "name": WORKBOOK_NAME})


def _header(sheets: FakeSheetsService, spreadsheet_id: str, tab: str) -> list[str]:
    rows = sheets.spreadsheets_by_id[spreadsheet_id].read(f"{quote_tab(tab)}!1:1")
    return rows[0] if rows else []


# --------------------------------------------------------------------
# A1 helpers
# --------------------------------------------------------------------


def test_column_letter_handles_wrap() -> None:
    assert column_letter(0) == "A"
    assert column_letter(25) == "Z"
    assert column_letter(26) == "AA"
    assert column_letter(51) == "AZ"
    with pytest.raises(ValueError):
        column_letter(-1)


def test_quote_tab_escapes_apostrophes() -> None:
    assert quote_tab("Settings") == "'Settings'"
    assert quote_tab("Bob's Tab") == "'Bob''s Tab'"


# --------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------


def test_creates_workbook_when_drive_has_none(services) -> None:
    sheets, drive = services
    info = ensure_workbook(sheets=sheets, drive=drive)

    assert info.created is True
    assert info.spreadsheet_id in sheets.spreadsheets_by_id
    assert info.url.endswith("/edit")
    assert info.changed is True


def test_creates_every_tab_in_the_schema(services) -> None:
    sheets, drive = services
    info = ensure_workbook(sheets=sheets, drive=drive)

    book = sheets.spreadsheets_by_id[info.spreadsheet_id]
    assert set(book.sheet_ids) == set(all_tab_names())


def test_writes_a_header_row_on_every_tab(services) -> None:
    sheets, drive = services
    info = ensure_workbook(sheets=sheets, drive=drive)

    for tab in WORKBOOK_TABS:
        assert _header(sheets, info.spreadsheet_id, tab.name) == list(tab.column_names)


def test_seeds_settings_with_safe_defaults(services) -> None:
    sheets, drive = services
    info = ensure_workbook(sheets=sheets, drive=drive)

    assert info.settings_seeded is True
    rows = sheets.spreadsheets_by_id[info.spreadsheet_id].read("'Settings'!A2:D")
    seeded = {row[0]: row[1] for row in rows}
    assert seeded["dry_run"] == "true"
    assert seeded["gmail_processing_enabled"] == "false"
    assert len(rows) == len(DEFAULT_SETTINGS)


def test_freezes_and_bolds_the_header_row(services) -> None:
    sheets, drive = services
    ensure_workbook(sheets=sheets, drive=drive)

    kinds = {k for request in sheets.batch_update_requests for k in request}
    assert "repeatCell" in kinds
    assert "updateSheetProperties" in kinds


# --------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------


def test_second_run_changes_nothing(services) -> None:
    sheets, drive = services
    first = ensure_workbook(sheets=sheets, drive=drive)
    _register_in_drive(drive, first.spreadsheet_id)

    second = ensure_workbook(sheets=sheets, drive=drive)

    assert second.spreadsheet_id == first.spreadsheet_id
    assert second.created is False
    assert second.tabs_created == []
    assert second.columns_added == {}
    assert second.settings_seeded is False
    assert second.changed is False


def test_second_run_does_not_create_a_duplicate_workbook(services) -> None:
    sheets, drive = services
    first = ensure_workbook(sheets=sheets, drive=drive)
    _register_in_drive(drive, first.spreadsheet_id)

    ensure_workbook(sheets=sheets, drive=drive)

    assert sheets.call_counts["create"] == 1
    assert len(sheets.spreadsheets_by_id) == 1


def test_second_run_does_not_duplicate_settings_rows(services) -> None:
    sheets, drive = services
    first = ensure_workbook(sheets=sheets, drive=drive)
    _register_in_drive(drive, first.spreadsheet_id)

    ensure_workbook(sheets=sheets, drive=drive)

    rows = sheets.spreadsheets_by_id[first.spreadsheet_id].read("'Settings'!A2:D")
    assert len(rows) == len(DEFAULT_SETTINGS)


def test_explicit_spreadsheet_id_skips_the_drive_lookup(services) -> None:
    sheets, drive = services
    first = ensure_workbook(sheets=sheets, drive=drive)

    second = ensure_workbook(
        sheets=sheets, drive=drive, spreadsheet_id=first.spreadsheet_id
    )

    assert second.spreadsheet_id == first.spreadsheet_id
    assert second.created is False
    # Only the very first call needed to search Drive.
    assert len(drive.queries) == 1


# --------------------------------------------------------------------
# Drift: new tabs and new columns
# --------------------------------------------------------------------


def test_adds_a_tab_the_user_deleted(services) -> None:
    sheets, drive = services
    info = ensure_workbook(sheets=sheets, drive=drive)
    _register_in_drive(drive, info.spreadsheet_id)

    book = sheets.spreadsheets_by_id[info.spreadsheet_id]
    del book.sheet_ids["Trips"]
    del book.grids["Trips"]

    second = ensure_workbook(sheets=sheets, drive=drive)

    assert second.tabs_created == ["Trips"]
    assert "Trips" in sheets.spreadsheets_by_id[info.spreadsheet_id].sheet_ids


def test_appends_new_schema_columns_without_reordering(services) -> None:
    """A user-reordered header keeps its order; missing columns land at the end."""
    sheets, drive = services
    book = sheets.seed(WORKBOOK_NAME)
    for tab in WORKBOOK_TABS:
        book.add_sheet(tab.name)
    # User reordered Settings and dropped two columns.
    book.write("'Settings'!A1", [["value", "key"]])
    _register_in_drive(drive, book.spreadsheet_id)

    info = ensure_workbook(sheets=sheets, drive=drive)

    assert info.created is False
    assert info.columns_added["Settings"] == ["description", "updated_at"]
    assert _header(sheets, book.spreadsheet_id, "Settings") == [
        "value",
        "key",
        "description",
        "updated_at",
    ]


def test_seeded_rows_follow_the_sheets_own_column_order(services) -> None:
    """Seeding must map by column name, not by schema position."""
    sheets, drive = services
    book = sheets.seed(WORKBOOK_NAME)
    for tab in WORKBOOK_TABS:
        book.add_sheet(tab.name)
    book.write("'Settings'!A1", [["value", "key"]])
    _register_in_drive(drive, book.spreadsheet_id)

    ensure_workbook(sheets=sheets, drive=drive)

    rows = book.read("'Settings'!A2:D")
    # Column A is `value`, column B is `key` — the sheet's order, not the schema's.
    by_key = {row[1]: row[0] for row in rows}
    assert by_key["dry_run"] == "true"
    assert by_key["gmail_processing_enabled"] == "false"


def test_existing_settings_rows_are_never_overwritten(services) -> None:
    sheets, drive = services
    info = ensure_workbook(sheets=sheets, drive=drive)
    _register_in_drive(drive, info.spreadsheet_id)

    book = sheets.spreadsheets_by_id[info.spreadsheet_id]
    book.write("'Settings'!B2", [["USER-EDITED"]])

    second = ensure_workbook(sheets=sheets, drive=drive)

    assert second.settings_seeded is False
    assert book.read("'Settings'!A2:D")[0][1] == "USER-EDITED"


# --------------------------------------------------------------------
# Drive lookup
# --------------------------------------------------------------------


def test_find_workbook_id_returns_none_when_absent(services) -> None:
    _sheets, drive = services
    assert find_workbook_id(drive) is None


def test_find_workbook_id_queries_for_spreadsheets_only(services) -> None:
    _sheets, drive = services
    find_workbook_id(drive)

    query = drive.queries[0]
    assert "application/vnd.google-apps.spreadsheet" in query
    assert WORKBOOK_NAME in query
    assert "trashed=false" in query


def test_find_workbook_id_picks_the_first_match(services) -> None:
    _sheets, drive = services
    drive.files_present = [
        {"id": "abc", "name": WORKBOOK_NAME},
        {"id": "def", "name": WORKBOOK_NAME},
    ]
    assert find_workbook_id(drive) == "abc"
