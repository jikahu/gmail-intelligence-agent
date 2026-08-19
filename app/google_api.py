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

#: One built ``Resource`` per (api, version) for the life of this process.
#: Building one means parsing Google's full API discovery document, which is
#: real memory/CPU work -- worth doing once, not on every single Gmail/Sheets/
#: Drive call (previously every dashboard load, and every 2-5 minutes from the
#: real-time poller and digest scheduler). The live ``Credentials`` object
#: inside a cached ``Resource`` already refreshes its own access token on
#: every API call as needed, so reusing the ``Resource`` doesn't risk serving
#: a stale token -- only an actual identity change (connect, reconnect,
#: disconnect) requires :func:`clear_service_cache`.
_service_cache: dict[tuple[str, str], Resource] = {}


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
    """Return an authenticated service for ``api`` at ``version``.

    Reuses a cached client for the rest of this process's life once built --
    see :data:`_service_cache`. Loads the stored token when one isn't supplied
    and there's no cached client yet. Raises :class:`NotConnectedError` if the
    account has never been connected.
    """
    cache_key = (api, version)
    cached = _service_cache.get(cache_key)
    if cached is not None:
        return cached

    stored = stored or load_stored_token_or_raise()
    creds = credentials_from_stored(stored)
    service = build(api, version, credentials=creds, cache_discovery=False)

    if creds.token and creds.token != stored.access_token:
        stored.access_token = creds.token
        if creds.expiry is not None:
            stored.expiry_iso = creds.expiry.isoformat()
        save_token(stored)
        log.info("google_access_token_refreshed", extra={"api": api})

    _service_cache[cache_key] = service
    return service


def clear_service_cache() -> None:
    """Drop every cached API client so the next call rebuilds from the
    current stored token.

    Call this whenever the stored token's *identity* changes -- a fresh OAuth
    connect, a reconnect, or a disconnect. Not needed for ordinary
    access-token refreshes, which the cached client's own ``Credentials``
    object already handles transparently.
    """
    _service_cache.clear()


__all__ = (
    "NotConnectedError",
    "build_service",
    "clear_service_cache",
    "load_stored_token_or_raise",
)
