"""Application configuration.

Loads values from environment variables (and an optional ``.env`` file) using
pydantic-settings. All safety-related defaults are conservative:

* ``dry_run`` defaults to ``True`` — nothing modifies Gmail unless explicitly disabled.
* ``gmail_processing_enabled`` defaults to ``False`` — Gmail is not touched at all until
  the user opts in.
* No AI keys are required to boot the app; classification just stays deterministic.

Callers should always use :func:`get_settings` rather than instantiating
``Settings`` directly, so the object is cached and environment reads happen once.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


AppEnv = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AIProvider = Literal["anthropic", "openai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: AppEnv = "development"
    log_level: LogLevel = "INFO"

    # Safety
    dry_run: bool = True
    gmail_processing_enabled: bool = False

    # AI
    ai_provider: AIProvider = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    #: Effort/quality hint passed to the provider. Classification is a short
    #: task, so "low" is the sensible default for cost and latency.
    ai_effort: str = "low"

    # Google OAuth
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/oauth/callback"

    # OAuth token durability on a host with an ephemeral filesystem, e.g.
    # Render's free tier. The local encrypted token file doesn't survive a
    # redeploy there, but a refresh token barely ever changes once issued —
    # so it's durably re-seeded from here instead of a paid persistent disk.
    # Render's own environment-variable store (unlike the container's local
    # disk) survives every redeploy. Optional — leave unset for local
    # development, where the local file is enough on its own.
    google_oauth_seed_refresh_token: str | None = None
    google_oauth_seed_account_email: str | None = None

    # Web session. Signs the OAuth CSRF `state` value and derives the key
    # that encrypts the stored refresh token on disk.
    session_secret: str = "change-me-to-a-long-random-string"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()


def reload_settings() -> Settings:
    """Clear the settings cache and reload from the environment.

    Only used by tests that need to re-read env vars mid-process.
    """
    get_settings.cache_clear()
    return get_settings()
