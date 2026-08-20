"""Config loading tests."""

from __future__ import annotations

import pytest

from app.config.settings import reload_settings


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DRY_RUN", "GMAIL_PROCESSING_ENABLED", "APP_ENV", "AI_PROVIDER"):
        monkeypatch.delenv(var, raising=False)

    settings = reload_settings()
    assert settings.dry_run is True
    assert settings.gmail_processing_enabled is False
    assert settings.app_env == "development"
    assert settings.ai_provider == "anthropic"


def test_env_overrides_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")

    settings = reload_settings()
    assert settings.dry_run is False
    assert settings.gmail_processing_enabled is True
    assert settings.ai_provider == "openai"
