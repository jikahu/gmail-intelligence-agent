"""Dashboard access control (Phase 8) — sessions and authorization.

These cover the security-critical logic without ever calling Google: who is
allowed in, and whether a session cookie is genuine and current.
"""

from __future__ import annotations

import pytest

from app.config import reload_settings
from app.dashboard import auth
from app.gmail.tokens import StoredToken, save_token
from tests.fixtures.emails import DEFAULT_USER


# --------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------


def test_session_round_trip_lowercases_email() -> None:
    token = auth.issue_session("Jikahu@Gmail.com")
    assert auth.read_session(token) == "jikahu@gmail.com"


def test_read_session_rejects_missing_and_empty() -> None:
    assert auth.read_session(None) is None
    assert auth.read_session("") is None


def test_read_session_rejects_tampered_token() -> None:
    token = auth.issue_session(DEFAULT_USER)
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    assert auth.read_session(tampered) is None


def test_read_session_rejects_foreign_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    token = auth.issue_session(DEFAULT_USER)
    # A cookie signed under a different secret must not validate.
    monkeypatch.setenv("SESSION_SECRET", "a-totally-different-secret-value")
    reload_settings()
    assert auth.read_session(token) is None


def test_read_session_honours_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    token = auth.issue_session(DEFAULT_USER)
    # Force every token to read as older than allowed.
    monkeypatch.setattr(auth, "_session_max_age_seconds", lambda: -1)
    assert auth.read_session(token) is None


# --------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------


def _connect(email: str = DEFAULT_USER) -> None:
    save_token(StoredToken(refresh_token="r", scopes=["s"], account_email=email))


def test_connected_account_is_authorized() -> None:
    _connect()
    assert auth.is_authorized(DEFAULT_USER) is True
    # Case-insensitive.
    assert auth.is_authorized("JIKAHU@gmail.com") is True


def test_other_accounts_are_not_authorized() -> None:
    _connect()
    assert auth.is_authorized("stranger@example.com") is False
    assert auth.is_authorized(None) is False
    assert auth.is_authorized("") is False


def test_no_connection_means_no_one_authorized() -> None:
    # No token saved.
    assert auth.authorized_emails() == set()
    assert auth.is_authorized(DEFAULT_USER) is False


def test_allowlist_adds_extra_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    _connect()
    monkeypatch.setenv(
        "DASHBOARD_AUTHORIZED_EMAILS", "Teammate@Example.com, second@example.com"
    )
    reload_settings()
    emails = auth.authorized_emails()
    assert "teammate@example.com" in emails
    assert "second@example.com" in emails
    assert DEFAULT_USER in emails
    assert auth.is_authorized("teammate@example.com") is True


def test_current_user_requires_valid_session_and_authorization() -> None:
    _connect()

    class _Req:
        def __init__(self, cookies: dict[str, str]) -> None:
            self.cookies = cookies

    good = auth.issue_session(DEFAULT_USER)
    assert auth.current_user(_Req({auth.SESSION_COOKIE: good})) == DEFAULT_USER

    # Valid signature, but the account isn't authorized → rejected.
    stranger = auth.issue_session("stranger@example.com")
    assert auth.current_user(_Req({auth.SESSION_COOKIE: stranger})) is None

    # No cookie at all.
    assert auth.current_user(_Req({})) is None
