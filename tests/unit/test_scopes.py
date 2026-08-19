"""Scope registry tests — the app must not silently request new permissions."""

from __future__ import annotations

from app.gmail.scopes import PHASE_1_SCOPES, SCOPE_DESCRIPTIONS, describe


def test_phase_1_scopes_are_exactly_the_expected_set() -> None:
    assert PHASE_1_SCOPES == (
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/contacts.readonly",
        "https://www.googleapis.com/auth/contacts.other.readonly",
    )


def test_phase_1_never_requests_write_scopes() -> None:
    forbidden = {
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/gmail.labels",
    }
    assert set(PHASE_1_SCOPES).isdisjoint(forbidden)


def test_every_requested_scope_has_a_description() -> None:
    for scope in PHASE_1_SCOPES:
        assert scope in SCOPE_DESCRIPTIONS, f"missing description for {scope}"
        assert SCOPE_DESCRIPTIONS[scope].strip(), f"empty description for {scope}"


def test_describe_returns_pairs_in_same_order() -> None:
    pairs = describe()
    assert [s for s, _ in pairs] == list(PHASE_1_SCOPES)
