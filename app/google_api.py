"""Authenticated Google API service construction, shared by all API clients.

Every Google client in this app needs the same three steps:

1. Load the encrypted OAuth token from disk.
2. Turn it into a live ``Credentials`` object.
3. If ``google-auth`` silently refreshed the access token while building the
   service, re-save the token so the next process boot skips a network refresh.

That logic lives here once so Gmail (Phase 1) and Sheets/Drive (Phase 2) can't
drift apart.
"""

from __future__ import annotations

from googleapiclient.discovery import Resource, build

from app.gmail.oauth import credentials_from_stored
from app.gmail.tokens import StoredToken, load_token, save_token
from app.logging_config import get_logger

log = get_logger("app.google_api")


class NotConnectedError(FileNotFoundError):
    """No Google account has been connected yet.

    Subclasses ``FileNotFoundError`` so Phase 1 callers that already catch that
    type keep working unchanged.
    """


def load_stored_token_or_raise() -> StoredToken:
    """Return the stored token, or raise :class:`NotConnectedError`."""
    stored = load_token()
    if stored is None:
        raise NotConnectedError(
            "No Google token found. Visit /oauth/start to connect the account."
        )
    return stored


def build_service(
    api: str, version: str, stored: StoredToken | None = None
) -> Resource:
    """Build an authenticated service for ``api`` at ``version``.

    Loads the stored token when one isn't supplied. Raises
    :class:`NotConnectedError` if the account has never been connected.
    """
    stored = stored or load_stored_token_or_raise()
    creds = credentials_from_stored(stored)
    service = build(api, version, credentials=creds, cache_discovery=False)

    if creds.token and creds.token != stored.access_token:
        stored.access_token = creds.token
        if creds.expiry is not None:
            stored.expiry_iso = creds.expiry.isoformat()
        save_token(stored)
        log.info("google_access_token_refreshed", extra={"api": api})

    return service


__all__ = (
    "NotConnectedError",
    "build_service",
    "load_stored_token_or_raise",
)
