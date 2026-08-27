"""OAuth flow tests — no real Google calls."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.gmail import oauth, tokens as tokens_module
from app.oauth_scopes import ACTIVE_SCOPES


@pytest.fixture(autouse=True)
def _oauth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SESSION_SECRET", "unit-test-secret-value-1234567890")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/callback")

    monkeypatch.setattr(tokens_module, "TOKEN_DIR", tmp_path / "oauth_tokens")
    monkeypatch.setattr(
        tokens_module, "TOKEN_FILE", tmp_path / "oauth_tokens" / "token.json.enc"
    )

    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_build_authorization_url_contains_expected_pieces() -> None:
    url, state = oauth.build_authorization_url()

    assert url.startswith("https://accounts.google.com/o/oauth2/auth")
    assert "client_id=test-client-id" in url
    # Redirect URI is url-encoded.
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Foauth%2Fcallback" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "scope=" in url
    assert "gmail.readonly" in url
    assert "contacts.other.readonly" in url
    # State value is signed and echoed back in the URL.
    assert state in url
    assert oauth.verify_state(state) is True


def test_verify_state_rejects_tampered_value() -> None:
    _, state = oauth.build_authorization_url()
    tampered = state[:-2] + "xx"
    assert oauth.verify_state(tampered) is False


def test_exchange_code_stores_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _, state = oauth.build_authorization_url()

    fake_creds = MagicMock()
    fake_creds.token = "ya29.access"
    fake_creds.refresh_token = "1//refresh"
    fake_creds.expiry = datetime(2026, 8, 14, tzinfo=timezone.utc).replace(tzinfo=None)
    fake_creds.scopes = list(ACTIVE_SCOPES)
    fake_creds.client_id = "test-client-id.apps.googleusercontent.com"

    fake_flow = MagicMock()
    fake_flow.credentials = fake_creds
    fake_flow.fetch_token = MagicMock()

    with patch.object(oauth, "_flow", return_value=fake_flow), patch.object(
        oauth, "_try_lookup_email", return_value="user@example.com"
    ):
        stored = oauth.exchange_code_for_token(code="auth-code", state=state)

    fake_flow.fetch_token.assert_called_once_with(code="auth-code")
    assert stored.refresh_token == "1//refresh"
    assert stored.account_email == "user@example.com"

    loaded = tokens_module.load_token()
    assert loaded is not None
    assert loaded.refresh_token == "1//refresh"


def test_exchange_code_rejects_bad_state() -> None:
    with pytest.raises(PermissionError):
        oauth.exchange_code_for_token(code="x", state="bogus-state-value")


def test_exchange_code_errors_without_refresh_token() -> None:
    _, state = oauth.build_authorization_url()

    fake_creds = MagicMock()
    fake_creds.token = "ya29.access"
    fake_creds.refresh_token = None
    fake_creds.expiry = None
    fake_creds.scopes = []
    fake_creds.client_id = "x"

    fake_flow = MagicMock()
    fake_flow.credentials = fake_creds

    with patch.object(oauth, "_flow", return_value=fake_flow):
        with pytest.raises(RuntimeError, match="refresh_token"):
            oauth.exchange_code_for_token(code="c", state=state)


def test_build_url_without_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        oauth.build_authorization_url()
