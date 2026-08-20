"""Scope aggregation tests.

The safety property under test: the app can never quietly acquire a
permission beyond the one, documented write scope it deliberately adds
(``gmail.modify`` — labels, archive, Trash; never send, and never a permanent
delete).
"""

from __future__ import annotations

from app.gmail.scopes import (
    GMAIL_LABEL_COLOR_SCOPES,
    GMAIL_WRITE_SCOPES,
    PHASE_1_SCOPES,
    PHASE_11_SCOPES,
)
from app.oauth_scopes import ACTIVE_SCOPES, describe, missing_from

#: Anything here would let the app send mail or destroy it beyond Trash —
#: none of this is ever requested.
FORBIDDEN_SCOPES = {
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://mail.google.com/",
}


def test_active_scopes_is_the_sum_of_registered_scopes() -> None:
    assert set(ACTIVE_SCOPES) == (set(PHASE_1_SCOPES) | set(PHASE_11_SCOPES))


def test_active_scopes_has_no_duplicates() -> None:
    assert len(ACTIVE_SCOPES) == len(set(ACTIVE_SCOPES))


def test_no_scope_beyond_the_one_documented_write_scope_is_ever_requested() -> None:
    assert not (set(ACTIVE_SCOPES) & FORBIDDEN_SCOPES)


def test_write_scopes_add_exactly_gmail_modify_and_nothing_else() -> None:
    """The one deliberate write scope this app ever asks for (CLAUDE.md §5)."""
    assert set(PHASE_11_SCOPES) == set(GMAIL_WRITE_SCOPES)
    assert PHASE_11_SCOPES == ("https://www.googleapis.com/auth/gmail.modify",)
    assert set(ACTIVE_SCOPES) - set(PHASE_1_SCOPES) == set(PHASE_11_SCOPES)


def test_gmail_labels_scope_stays_out_of_active_scopes() -> None:
    """gmail.labels (defined for the label-color feature) is deliberately NOT
    requested yet: production testing showed Google rejects *every* token
    refresh with 'invalid_scope' when it's included, because the scope was
    never registered on this OAuth client's consent screen in Google Cloud
    Console. Re-adding this to ACTIVE_SCOPES must be a deliberate act once
    that's fixed, never an accidental one -- this test is the tripwire."""
    assert GMAIL_LABEL_COLOR_SCOPES == ("https://www.googleapis.com/auth/gmail.labels",)
    assert not (set(ACTIVE_SCOPES) & set(GMAIL_LABEL_COLOR_SCOPES))


def test_every_active_scope_has_a_description() -> None:
    for scope, description in describe():
        assert description != "(no description)", f"Undocumented scope: {scope}"
        assert description.strip()


def test_missing_from_detects_a_pre_write_grant() -> None:
    """A token issued before write access was added is missing the write
    scope and must reconnect before any Gmail write is attempted
    (CLAUDE.md §18)."""
    missing = missing_from(list(PHASE_1_SCOPES))
    assert set(missing) == set(PHASE_11_SCOPES)


def test_missing_from_is_empty_for_a_full_grant() -> None:
    assert missing_from(list(ACTIVE_SCOPES)) == []


def test_missing_from_handles_none() -> None:
    assert set(missing_from(None)) == set(ACTIVE_SCOPES)
