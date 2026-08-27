"""Integration-ish tests for the OAuth-related HTTP routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def credentialed_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/callback")
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    return TestClient(create_app())


def test_landing_page_shows_connect_button_when_disconnected(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Connect Gmail" in resp.text
    assert "gmail.readonly" in resp.text


def test_oauth_status_reports_disconnected(client: TestClient) -> None:
    resp = client.get("/oauth/status")
    assert resp.status_code == 200
    assert resp.json() == {"connected": False}


def test_oauth_start_redirects_to_google(credentialed_client: TestClient) -> None:
    resp = credentialed_client.get("/oauth/start", follow_redirects=False)
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/auth")
    assert "gmail.readonly" in location


def test_oauth_start_without_credentials_returns_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    resp = client.get("/oauth/start", follow_redirects=False)
    assert resp.status_code == 500


def test_oauth_callback_reports_google_error(client: TestClient) -> None:
    resp = client.get("/oauth/callback?error=access_denied")
    assert resp.status_code == 400
    assert "access_denied" in resp.text


def test_oauth_callback_rejects_missing_state(client: TestClient) -> None:
    resp = client.get("/oauth/callback?code=abc")
    assert resp.status_code == 400
    assert "state" in resp.text.lower() or "code" in resp.text.lower()


def test_gmail_preview_requires_token(client: TestClient) -> None:
    resp = client.get("/gmail/preview")
    assert resp.status_code == 409
    assert "oauth/start" in resp.text.lower()


def test_disconnect_has_no_note_without_a_seed(client: TestClient) -> None:
    resp = client.post("/oauth/disconnect")
    assert resp.status_code == 200
    assert resp.json() == {"disconnected": False, "note": None}


def test_disconnect_warns_when_a_seed_is_still_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_SEED_REFRESH_TOKEN", "1//still-configured")
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    local_client = TestClient(create_app())

    resp = local_client.post("/oauth/disconnect")

    assert resp.status_code == 200
    body = resp.json()
    assert body["note"] is not None
    assert "GOOGLE_OAUTH_SEED_REFRESH_TOKEN" in body["note"]


def _patch_successful_exchange(monkeypatch: pytest.MonkeyPatch, refresh_token: str) -> None:
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from app.gmail import oauth
    from app.oauth_scopes import ACTIVE_SCOPES

    fake_creds = MagicMock()
    fake_creds.token = "ya29.access"
    fake_creds.refresh_token = refresh_token
    fake_creds.expiry = datetime(2026, 8, 14, tzinfo=timezone.utc).replace(tzinfo=None)
    fake_creds.scopes = list(ACTIVE_SCOPES)
    fake_creds.client_id = "test-client-id.apps.googleusercontent.com"

    fake_flow = MagicMock()
    fake_flow.credentials = fake_creds
    fake_flow.fetch_token = MagicMock()

    monkeypatch.setattr(oauth, "_flow", lambda: fake_flow)
    monkeypatch.setattr(oauth, "_try_lookup_email", lambda creds: "user@example.com")


def test_oauth_callback_reveals_refresh_token_once(
    credentialed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.gmail import oauth

    _, state = oauth.build_authorization_url()
    _patch_successful_exchange(monkeypatch, "1//brand-new-refresh-token")

    resp = credentialed_client.get(f"/oauth/callback?code=abc&state={state}")

    assert resp.status_code == 200
    assert "1//brand-new-refresh-token" in resp.text
    assert "GOOGLE_OAUTH_SEED_REFRESH_TOKEN" in resp.text


def test_oauth_callback_hides_reveal_when_seed_already_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing new to copy when the live seed already equals the fresh token.

    Builds its own client (rather than using the ``credentialed_client``
    fixture) because ``create_app()`` captures ``settings`` once at app-build
    time — the seed env var has to be set *before* the app is constructed for
    the route to see it.
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/callback")
    monkeypatch.setenv("GOOGLE_OAUTH_SEED_REFRESH_TOKEN", "1//already-configured")
    from app.config import get_settings
    from app.gmail import oauth
    from app.main import create_app

    get_settings.cache_clear()
    local_client = TestClient(create_app())

    _, state = oauth.build_authorization_url()
    _patch_successful_exchange(monkeypatch, "1//already-configured")

    resp = local_client.get(f"/oauth/callback?code=abc&state={state}")

    assert resp.status_code == 200
    assert "1//already-configured" not in resp.text
    assert "GOOGLE_OAUTH_SEED_REFRESH_TOKEN" not in resp.text
