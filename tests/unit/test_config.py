"""Config loading tests."""

from __future__ import annotations

import pytest

from app.config.settings import Settings, reload_settings


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "DRY_RUN", "GMAIL_PROCESSING_ENABLED", "APP_ENV",
        "DIGEST_TIMEZONE", "DIGEST_HOUR", "AI_PROVIDER",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = reload_settings()
    assert settings.dry_run is True
    assert settings.gmail_processing_enabled is False
    assert settings.app_env == "development"
    assert settings.digest_timezone == "America/New_York"
    assert settings.digest_hour == 0
    assert settings.ai_provider == "anthropic"


def test_env_overrides_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("DIGEST_HOUR", "6")

    settings = reload_settings()
    assert settings.dry_run is False
    assert settings.gmail_processing_enabled is True
    assert settings.ai_provider == "openai"
    assert settings.digest_hour == 6


def test_invalid_timezone_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIGEST_TIMEZONE", "Not/A_Real_Zone")
    with pytest.raises(ValueError):
        reload_settings()


def test_digest_tz_property_returns_zoneinfo() -> None:
    settings = Settings(digest_timezone="America/New_York")
    tz = settings.digest_tz
    assert tz.key == "America/New_York"


def test_digest_hour_bounds_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIGEST_HOUR", "24")
    with pytest.raises(ValueError):
        reload_settings()
