"""Encrypted token store tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.gmail import tokens as tokens_module
from app.gmail.tokens import StoredToken, clear_token, load_token, save_token, token_exists


@pytest.fixture(autouse=True)
def _isolated_token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the token file into a tmp dir and set a stable SESSION_SECRET."""
    monkeypatch.setenv("SESSION_SECRET", "unit-test-session-secret-do-not-use-in-prod")
    monkeypatch.setattr(tokens_module, "TOKEN_DIR", tmp_path / "oauth_tokens")
    monkeypatch.setattr(
        tokens_module, "TOKEN_FILE", tmp_path / "oauth_tokens" / "token.json.enc"
    )
    yield


def _sample_token() -> StoredToken:
    return StoredToken(
        refresh_token="1//refresh-token-xyz",
        access_token="ya29.access-token-abc",
        expiry_iso="2026-08-14T00:00:00+00:00",
        scopes=["openid", "https://www.googleapis.com/auth/gmail.readonly"],
        account_email="user@example.com",
        client_id="client-id-abc.apps.googleusercontent.com",
    )


def test_save_load_round_trips() -> None:
    save_token(_sample_token())
    loaded = load_token()
    assert loaded is not None
    assert loaded.refresh_token == "1//refresh-token-xyz"
    assert loaded.account_email == "user@example.com"


def test_stored_file_is_not_plaintext() -> None:
    save_token(_sample_token())
    raw = tokens_module.TOKEN_FILE.read_bytes()
    assert b"refresh-token-xyz" not in raw
    assert b"access-token-abc" not in raw
    assert b"user@example.com" not in raw


def test_load_returns_none_when_missing() -> None:
    assert load_token() is None


def test_clear_token_removes_file() -> None:
    save_token(_sample_token())
    assert token_exists()
    assert clear_token() is True
    assert not token_exists()
    assert clear_token() is False


def test_wrong_secret_yields_none_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    save_token(_sample_token())
    monkeypatch.setenv("SESSION_SECRET", "a-completely-different-secret-value")
    # Reset the settings cache so the new secret is picked up.
    from app.config import get_settings

    get_settings.cache_clear()
    assert load_token() is None


def test_placeholder_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "change-me-to-a-long-random-string")
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        save_token(_sample_token())


# --------------------------------------------------------------------
# Phase 16 — reseeding from GOOGLE_OAUTH_SEED_REFRESH_TOKEN
# --------------------------------------------------------------------


def _clear_settings_cache() -> None:
    from app.config import get_settings

    get_settings.cache_clear()


def test_load_seeds_from_env_when_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_SEED_REFRESH_TOKEN", "1//seeded-refresh-token")
    monkeypatch.setenv("GOOGLE_OAUTH_SEED_ACCOUNT_EMAIL", "user@example.com")
    _clear_settings_cache()

    assert not token_exists()
    loaded = load_token()

    assert loaded is not None
    assert loaded.refresh_token == "1//seeded-refresh-token"
    assert loaded.account_email == "user@example.com"
    assert loaded.access_token is None
    # The seed is persisted locally so subsequent reads/refreshes don't need
    # the env var again this process.
    assert token_exists()


def test_no_seed_configured_still_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_SEED_REFRESH_TOKEN", raising=False)
    _clear_settings_cache()
    assert load_token() is None
    assert not token_exists()


def test_existing_local_file_wins_over_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real, current local token is never clobbered by a static env seed."""
    save_token(_sample_token())
    monkeypatch.setenv("GOOGLE_OAUTH_SEED_REFRESH_TOKEN", "1//a-different-seeded-token")
    _clear_settings_cache()

    loaded = load_token()
    assert loaded is not None
    assert loaded.refresh_token == "1//refresh-token-xyz"


def test_reseeds_after_undecryptable_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrong-secret / corrupted file falls back to the env seed, not None."""
    save_token(_sample_token())
    monkeypatch.setenv("SESSION_SECRET", "a-completely-different-secret-value")
    monkeypatch.setenv("GOOGLE_OAUTH_SEED_REFRESH_TOKEN", "1//recovery-refresh-token")
    _clear_settings_cache()

    loaded = load_token()
    assert loaded is not None
    assert loaded.refresh_token == "1//recovery-refresh-token"
