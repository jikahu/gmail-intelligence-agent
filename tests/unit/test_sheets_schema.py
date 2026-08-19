"""Control workbook schema tests (CLAUDE.md §12)."""

from __future__ import annotations

import pytest

from app.sheets.schema import (
    AUDIT_LOG_TAB,
    DEFAULT_SETTINGS,
    SETTINGS_TAB,
    WORKBOOK_TABS,
    Column,
    Tab,
    all_tab_names,
    tab_by_name,
    validate_schema,
)

#: Every tab CLAUDE.md §12 requires.
REQUIRED_TABS = {
    "Settings",
    "VIPs",
    "Sender_Rules",
    "Domain_Rules",
    "Learned_Rule_Suggestions",
    "Review_Feedback",
    "Audit_Log",
    "Deadlines",
    "Subscriptions",
    "Trips",
    "System_Runs",
}


def test_every_required_tab_exists() -> None:
    assert REQUIRED_TABS.issubset(set(all_tab_names()))


def test_schema_validates() -> None:
    validate_schema()  # Raises on duplicate tab or column names.


def test_validate_rejects_duplicate_tabs(monkeypatch: pytest.MonkeyPatch) -> None:
    dupe = Tab(name="Settings", columns=(Column("key"),))
    monkeypatch.setattr(
        "app.sheets.schema.WORKBOOK_TABS", (*WORKBOOK_TABS, dupe)
    )
    with pytest.raises(ValueError, match="Duplicate tab name"):
        validate_schema()


def test_validate_rejects_duplicate_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = Tab(name="Bad_Tab", columns=(Column("id"), Column("id")))
    monkeypatch.setattr("app.sheets.schema.WORKBOOK_TABS", (bad,))
    with pytest.raises(ValueError, match="Duplicate column"):
        validate_schema()


def test_tab_by_name_round_trips() -> None:
    assert tab_by_name("Settings") is SETTINGS_TAB
    with pytest.raises(KeyError):
        tab_by_name("Nope")


def test_audit_log_captures_reversibility_fields() -> None:
    """Undo Last Run (CLAUDE.md §13) needs before/after state on every row."""
    columns = set(AUDIT_LOG_TAB.column_names)
    for required in (
        "run_id",
        "labels_before",
        "labels_after",
        "inbox_before",
        "inbox_after",
        "reversible",
        "undo_status",
    ):
        assert required in columns


def test_default_settings_are_safe() -> None:
    """The workbook must boot into dry-run with Gmail processing off."""
    defaults = {key: value for key, value, _desc in DEFAULT_SETTINGS}
    assert defaults["dry_run"] == "true"
    assert defaults["gmail_processing_enabled"] == "false"
    assert defaults["digest_timezone"] == "America/New_York"


def test_default_settings_keys_are_unique() -> None:
    keys = [key for key, _v, _d in DEFAULT_SETTINGS]
    assert len(keys) == len(set(keys))


def test_settings_tab_has_key_value_shape() -> None:
    assert SETTINGS_TAB.column_names[:2] == ("key", "value")
