"""Phase 16 — the app refuses to boot in production with unsafe defaults."""

from __future__ import annotations

import pytest


def test_production_with_placeholder_secret_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "change-me-to-a-long-random-string")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app()


def test_production_with_real_secret_boots_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a-real-generated-secret-value-not-the-placeholder")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    assert app is not None


def test_development_with_placeholder_secret_still_boots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard only fires in production — local dev keeps working out of the box."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SESSION_SECRET", "change-me-to-a-long-random-string")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    assert app is not None
