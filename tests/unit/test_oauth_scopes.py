"""Scope aggregation tests.

The safety property under test: the app can never quietly acquire a
permission beyond the one, documented write scope Phase 11 deliberately adds
(``gmail.modify`` — labels, archive, Trash; never send, and never a permanent
delete), and can never acquire full Drive access when the narrow
``drive.file`` scope is what's documented.
"""

from __future__ import annotations

from app.gmail.scopes import GMAIL_WRITE_SCOPES, PHASE_1_SCOPES, PHASE_11_SCOPES
from app.oauth_scopes import ACTIVE_SCOPES, describe, missing_from
from app.sheets.scopes import PHASE_2_SCOPES

#: Anything here would let the app send mail or destroy it beyond Trash —
#: none of this is ever requested, in any phase.
FORBIDDEN_SCOPES = {
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://mail.google.com/",
}

#: Full-Drive scopes. We only ever want drive.file.
FORBIDDEN_DRIVE_SCOPES = {
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata",
}


def test_active_scopes_is_the_sum_of_registered_phases() -> None:
    assert set(ACTIVE_SCOPES) == (
        set(PHASE_1_SCOPES) | set(PHASE_2_SCOPES) | set(PHASE_11_SCOPES)
    )


def test_active_scopes_has_no_duplicates() -> None:
    assert len(ACTIVE_SCOPES) == len(set(ACTIVE_SCOPES))


def test_no_scope_beyond_the_one_documented_write_scope_is_ever_requested() -> None:
    assert not (set(ACTIVE_SCOPES) & FORBIDDEN_SCOPES)


def test_phase_11_adds_exactly_gmail_modify_and_nothing_else() -> None:
    """The one deliberate write scope this app ever asks for (CLAUDE.md §5)."""
    assert set(PHASE_11_SCOPES) == set(GMAIL_WRITE_SCOPES)
    assert PHASE_11_SCOPES == ("https://www.googleapis.com/auth/gmail.modify",)
    assert set(ACTIVE_SCOPES) - (set(PHASE_1_SCOPES) | set(PHASE_2_SCOPES)) == set(
        PHASE_11_SCOPES
    )


def test_only_the_narrow_drive_scope_is_requested() -> None:
    assert "https://www.googleapis.com/auth/drive.file" in ACTIVE_SCOPES
    assert not (set(ACTIVE_SCOPES) & FORBIDDEN_DRIVE_SCOPES)


def test_phase_2_adds_sheets_access() -> None:
    assert "https://www.googleapis.com/auth/spreadsheets" in ACTIVE_SCOPES


def test_every_active_scope_has_a_description() -> None:
    for scope, description in describe():
        assert description != "(no description)", f"Undocumented scope: {scope}"
        assert description.strip()


def test_missing_from_detects_a_stale_grant() -> None:
    """A token issued before Phase 2 must be reported as needing re-consent."""
    missing = missing_from(list(PHASE_1_SCOPES) + list(PHASE_11_SCOPES))
    assert set(missing) == set(PHASE_2_SCOPES)


def test_missing_from_detects_a_pre_phase_11_grant() -> None:
    """A token issued before Phase 11 is missing the write scope and must
    reconnect before any Gmail write is attempted (CLAUDE.md §18)."""
    missing = missing_from(list(PHASE_1_SCOPES) + list(PHASE_2_SCOPES))
    assert set(missing) == set(PHASE_11_SCOPES)


def test_missing_from_is_empty_for_a_full_grant() -> None:
    assert missing_from(list(ACTIVE_SCOPES)) == []


def test_missing_from_handles_none() -> None:
    assert set(missing_from(None)) == set(ACTIVE_SCOPES)
