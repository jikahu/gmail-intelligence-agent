"""app.google_api::build_service caches one Resource per (api, version) for
the life of the process, instead of rebuilding (re-parsing Google's API
discovery document) on every single call -- the fix for the Render free-tier
memory-limit alert triggered by the background poller/digest scheduler
rebuilding a client every 2-5 minutes on top of every dashboard/API request.
"""

from __future__ import annotations

import app.google_api as google_api
from app.gmail.tokens import StoredToken


def _stored(access_token: str = "tok-1") -> StoredToken:
    return StoredToken(
        refresh_token="refresh-1",
        access_token=access_token,
        expiry_iso=None,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        client_id="client-1",
    )


def _fake_credentials(stored: StoredToken):
    class _Creds:
        token = stored.access_token
        expiry = None

    return _Creds()


def _patch_build(monkeypatch, calls: list[tuple[str, str]]):
    def _build(api, version, credentials=None, cache_discovery=False):
        calls.append((api, version))
        return object()

    monkeypatch.setattr(google_api, "build", _build)
    monkeypatch.setattr(google_api, "credentials_from_stored", _fake_credentials)


def test_build_service_reuses_cached_client(monkeypatch):
    google_api.clear_service_cache()
    calls: list[tuple[str, str]] = []
    _patch_build(monkeypatch, calls)

    first = google_api.build_service("gmail", "v1", stored=_stored())
    second = google_api.build_service("gmail", "v1", stored=_stored())

    assert first is second
    assert calls == [("gmail", "v1")]


def test_build_service_caches_independently_per_api(monkeypatch):
    google_api.clear_service_cache()
    calls: list[tuple[str, str]] = []
    _patch_build(monkeypatch, calls)

    gmail_service = google_api.build_service("gmail", "v1", stored=_stored())
    sheets_service = google_api.build_service("sheets", "v4", stored=_stored())

    assert gmail_service is not sheets_service
    assert calls == [("gmail", "v1"), ("sheets", "v4")]


def test_clear_service_cache_forces_rebuild(monkeypatch):
    google_api.clear_service_cache()
    calls: list[tuple[str, str]] = []
    _patch_build(monkeypatch, calls)

    google_api.build_service("gmail", "v1", stored=_stored())
    google_api.clear_service_cache()
    google_api.build_service("gmail", "v1", stored=_stored())

    assert calls == [("gmail", "v1"), ("gmail", "v1")]


def test_disconnect_clears_the_cache(monkeypatch, tmp_path):
    """clear_token() (the /oauth/disconnect path) must not keep serving a
    cached client built from the account that was just disconnected."""
    from app.gmail import tokens as tokens_module

    monkeypatch.setattr(tokens_module, "TOKEN_DIR", tmp_path / "oauth_tokens")
    monkeypatch.setattr(tokens_module, "TOKEN_FILE", tmp_path / "oauth_tokens" / "token.json.enc")

    google_api.clear_service_cache()
    calls: list[tuple[str, str]] = []
    _patch_build(monkeypatch, calls)

    google_api.build_service("gmail", "v1", stored=_stored())
    assert google_api._service_cache  # cache populated

    tokens_module.clear_token()

    assert google_api._service_cache == {}
