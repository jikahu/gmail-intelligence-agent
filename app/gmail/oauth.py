"""Google OAuth 2.0 flow (authorization code, offline access).

The flow:

1. ``build_authorization_url()`` returns the URL to send the user to. We embed
   a signed random ``state`` value (via ``itsdangerous``) so we can verify on
   the callback that the response is genuinely one of ours (CSRF defense).
2. Google redirects back to ``GOOGLE_OAUTH_REDIRECT_URI`` with ``code`` and
   ``state``. ``exchange_code_for_token()`` validates the state and swaps the
   code for an access + refresh token.
3. We persist a ``StoredToken`` (via ``app.gmail.tokens``) and use
   ``credentials_from_stored()`` to hand off a ready-to-use ``Credentials``
   object to the Gmail/People clients.

Refresh is handled automatically by ``google.auth`` when the client uses
``AuthorizedHttp``/``build()``. When a refresh happens we re-save the token so
the next process boot has the latest ``access_token`` cached.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings
from app.gmail.tokens import StoredToken, save_token
from app.logging_config import get_logger
from app.oauth_scopes import ACTIVE_SCOPES

log = get_logger("app.gmail.oauth")

_STATE_SALT = "gmail-agent.oauth-state"
_STATE_MAX_AGE_SECONDS = 600  # 10 minutes


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().session_secret,
        salt=_STATE_SALT,
    )


def _client_config() -> dict[str, Any]:
    s = get_settings()
    if not s.google_client_id or not s.google_client_secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in the "
            "environment (see .env.example)."
        )
    return {
        "web": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [s.google_oauth_redirect_uri],
        }
    }


def _allow_insecure_transport_for_local_dev(redirect_uri: str, app_env: str) -> None:
    """Let oauthlib accept a plain-``http://`` loopback redirect URI.

    ``google-auth-oauthlib`` refuses to exchange a code unless the redirect
    URI is HTTPS, even for ``localhost``. That's the right default, so this
    only relaxes it for a non-production app running against ``http://`` —
    Render/production always uses HTTPS, so this is a no-op there.
    """
    if app_env != "production" and redirect_uri.startswith("http://"):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


def _flow() -> Flow:
    s = get_settings()
    _allow_insecure_transport_for_local_dev(s.google_oauth_redirect_uri, s.app_env)
    flow = Flow.from_client_config(
        client_config=_client_config(),
        scopes=list(ACTIVE_SCOPES),
        redirect_uri=s.google_oauth_redirect_uri,
        # We build a fresh Flow object for the callback (a separate process
        # request from the one that started the flow), so there's no way to
        # hand it the code_verifier the library would auto-generate for PKCE
        # — that only ever lived on the throwaway object from /oauth/start.
        # PKCE protects public clients that can't hold a secret; this app
        # already authenticates with client_secret, so it isn't needed here.
        autogenerate_code_verifier=False,
    )
    return flow


def build_authorization_url() -> tuple[str, str]:
    """Return ``(authorization_url, signed_state)`` for a fresh OAuth attempt.

    ``prompt='consent'`` ensures Google always returns a ``refresh_token``
    even for accounts that have previously authorized this app.
    """
    nonce = secrets.token_urlsafe(24)
    signed_state = _serializer().dumps({"n": nonce})
    flow = _flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        prompt="consent",
        state=signed_state,
    )
    log.info("oauth_authorization_url_built")
    return auth_url, signed_state


def verify_state(state: str) -> bool:
    """Verify that the callback ``state`` was one we signed recently."""
    try:
        _serializer().loads(state, max_age=_STATE_MAX_AGE_SECONDS)
    except BadSignature:
        log.warning("oauth_state_invalid")
        return False
    return True


def exchange_code_for_token(code: str, state: str) -> StoredToken:
    """Exchange the auth code for tokens, persist, and return the stored token.

    Raises ``PermissionError`` if the ``state`` value can't be verified.
    """
    if not verify_state(state):
        raise PermissionError("OAuth state validation failed.")

    flow = _flow()
    flow.fetch_token(code=code)
    creds: Credentials = flow.credentials

    stored = StoredToken(
        refresh_token=creds.refresh_token or "",
        access_token=creds.token,
        expiry_iso=creds.expiry.replace(tzinfo=timezone.utc).isoformat() if creds.expiry else None,
        scopes=list(creds.scopes or []),
        client_id=creds.client_id,
    )
    if not stored.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh_token. This usually means the user "
            "had a prior grant. Revoke the app at "
            "https://myaccount.google.com/permissions and re-run the flow."
        )

    # Best-effort userinfo lookup. Non-fatal — the flow succeeds either way.
    stored.account_email = _try_lookup_email(creds)

    save_token(stored)
    log.info(
        "oauth_token_stored",
        extra={"account_email": stored.account_email, "scopes": stored.scopes},
    )
    return stored


def credentials_from_stored(stored: StoredToken) -> Credentials:
    """Turn a StoredToken back into a live ``Credentials`` object."""
    s = get_settings()
    creds = Credentials(
        token=stored.access_token,
        refresh_token=stored.refresh_token,
        token_uri=stored.token_uri,
        client_id=stored.client_id or s.google_client_id,
        client_secret=s.google_client_secret,
        scopes=list(stored.scopes) if stored.scopes else list(ACTIVE_SCOPES),
    )
    if stored.expiry_iso:
        expiry = datetime.fromisoformat(stored.expiry_iso)
        if expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        creds.expiry = expiry
    return creds


def _try_lookup_email(creds: Credentials) -> str | None:
    try:
        from googleapiclient.discovery import build

        service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        info = service.userinfo().get().execute()
        return info.get("email")
    except Exception as exc:  # noqa: BLE001 — non-fatal
        log.warning("oauth_userinfo_lookup_failed", extra={"error": str(exc)})
        return None


__all__ = (
    "build_authorization_url",
    "verify_state",
    "exchange_code_for_token",
    "credentials_from_stored",
)
