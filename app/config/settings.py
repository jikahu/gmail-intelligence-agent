"""Application configuration.

Loads values from environment variables (and an optional ``.env`` file) using
pydantic-settings. All safety-related defaults are conservative:

* ``dry_run`` defaults to ``True`` — nothing modifies Gmail unless explicitly disabled.
* ``gmail_processing_enabled`` defaults to ``False`` — Gmail is not touched at all until
  the user opts in.
* No AI keys are required to boot the app; they are only needed once Phase 4 lands.

Callers should always use :func:`get_settings` rather than instantiating
``Settings`` directly, so the object is cached and environment reads happen once.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
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

    # Digest scheduling
    digest_timezone: str = "America/New_York"
    digest_hour: int = Field(default=0, ge=0, le=23)
    #: Unlike realtime_enabled, this defaults on: the background check is a
    #: cheap clock comparison (no Gmail/Sheets call, no AI spend) unless it's
    #: actually time to build the digest, and it never writes to Gmail — see
    #: docs/TECHNICAL_STATUS.md's Phase 14 notes for the reasoning.
    digest_scheduler_enabled: bool = True

    # AI (wired in Phase 4). Model names are overridable from the workbook's
    # Settings tab — these are only the fallbacks.
    ai_provider: AIProvider = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    #: Effort/quality hint passed to the provider. Classification is a short
    #: task, so "low" is the sensible default for cost and latency.
    ai_effort: str = "low"

    # Google OAuth (wired in Phase 1)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/oauth/callback"

    # Sheets control workbook (wired in Phase 2)
    sheets_workbook_id: str | None = None

    # OAuth token durability on a host with an ephemeral filesystem, e.g.
    # Render's free tier (wired in Phase 16). The local encrypted token file
    # doesn't survive a redeploy there, but a refresh token barely ever
    # changes once issued — so it's durably re-seeded from here instead of a
    # paid persistent disk. Render's own environment-variable store (unlike
    # the container's local disk) survives every redeploy. Optional — leave
    # unset for local development, where the local file is enough on its own.
    google_oauth_seed_refresh_token: str | None = None
    google_oauth_seed_account_email: str | None = None

    # Web session
    session_secret: str = "change-me-to-a-long-random-string"

    # Dashboard (Phase 8). Only the connected Google account may sign in by
    # default. ``dashboard_authorized_emails`` is the seam for adding more
    # accounts later without building multi-tenant infrastructure — a
    # comma-separated allowlist, empty by default.
    dashboard_authorized_emails: str = ""
    dashboard_session_max_age_hours: int = Field(default=12, ge=1, le=720)
    #: Google redirects the dashboard sign-in here. Must be registered as an
    #: authorized redirect URI on the OAuth client (alongside the Gmail one).
    dashboard_login_redirect_uri: str = (
        "http://localhost:8000/dashboard/auth/callback"
    )

    # Classification
    review_confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)

    # Near-real-time processing (Phase 13). Off by default — the same
    # conservative-until-opted-in pattern as dry_run/gmail_processing_enabled.
    # Turning this on starts a background poll loop; it does not by itself
    # allow Gmail writes — check_write_gate still applies to every write this
    # loop attempts, exactly like every other write path in the app.
    realtime_enabled: bool = False
    realtime_poll_interval_seconds: int = Field(default=120, ge=30, le=3600)

    @field_validator("digest_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Unknown timezone {value!r}. Use a TZ database name like 'America/New_York'."
            ) from exc
        return value

    @property
    def digest_tz(self) -> ZoneInfo:
        """Return the digest timezone as a ZoneInfo object."""
        return ZoneInfo(self.digest_timezone)

    @property
    def dashboard_authorized_email_list(self) -> list[str]:
        """Explicit dashboard allowlist, lower-cased and de-blanked."""
        return [
            email.strip().lower()
            for email in self.dashboard_authorized_emails.split(",")
            if email.strip()
        ]

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
