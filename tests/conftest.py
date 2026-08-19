"""Shared pytest fixtures for the Gmail Intelligence Agent."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Ensure each test reads env vars fresh."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let Settings read the developer's real ``.env`` off disk.

    Without this, ``monkeypatch.delenv("GOOGLE_CLIENT_ID")`` etc. only clears
    the process environment — pydantic-settings' dotenv source still falls
    back to whatever is actually saved in ``.env``, so tests that assert
    "raises when credentials are missing" silently depend on the developer's
    local secrets being blank.
    """
    from app.config.settings import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _logging_enabled_at_info() -> None:
    """Force INFO so ``log.info(...)`` really builds a LogRecord in tests.

    Python's logging raises if an ``extra`` key collides with a reserved
    LogRecord attribute (``created``, ``module``, ``name``, ...), but only when
    the level is actually enabled. Without this, such a bug hides in tests and
    crashes in production, where logging is configured at INFO.
    """
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    yield
    root.setLevel(previous)


@pytest.fixture(autouse=True)
def _isolated_token_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may touch the real oauth_tokens/ directory."""
    from app.gmail import tokens as tokens_module

    monkeypatch.setattr(tokens_module, "TOKEN_DIR", tmp_path / "oauth_tokens")
    monkeypatch.setattr(
        tokens_module, "TOKEN_FILE", tmp_path / "oauth_tokens" / "token.json.enc"
    )
    # Give every test a working session secret so token save/load doesn't refuse.
    monkeypatch.setenv("SESSION_SECRET", "unit-test-session-secret-do-not-use-in-prod")
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())
