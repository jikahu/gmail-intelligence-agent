"""Dashboard access control (CLAUDE.md §13).

The Command Center is a web page that shows your email. On a public host
(Render) that page must be locked to *you*. This module is the lock.

Two independent pieces:

1. **Sessions.** A small signed cookie proves a browser has already signed in.
   We sign it with the app's ``session_secret`` (via ``itsdangerous``), the same
   mechanism the OAuth ``state`` uses, so a forged or tampered cookie is
   rejected and an old one expires on its own.

2. **Authorization.** "Signed in" is not enough — the signed-in Google account
   must be *allowed*. V1 authorizes exactly one account: whoever connected
   Gmail. ``dashboard_authorized_emails`` is the seam for adding more accounts
   later (a config list), so multi-account support won't need a rewrite — but we
   are deliberately **not** building multi-tenant infrastructure now (§13).

The actual "Sign in with Google" round-trip lives here too, but the only part
that talks to the network — :func:`complete_login` — is a single function, so
the security logic around it (who's allowed, is the cookie valid) is testable
without ever calling Google.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings
from app.gmail.tokens import load_token
from app.logging_config import get_logger

log = get_logger("app.dashboard.auth")

#: Name of the cookie carrying the signed session.
SESSION_COOKIE = "gmail_agent_session"

_SESSION_SALT = "gmail-agent.dashboard-session"
_STATE_SALT = "gmail-agent.dashboard-login-state"
_STATE_MAX_AGE_SECONDS = 600  # 10 minutes

#: Identity-only scopes. The sign-in learns *who* you are and nothing more — it
#: never asks for mailbox access (that's the separate Gmail grant).
LOGIN_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]


# --------------------------------------------------------------------
# Authorization — who is allowed in
# --------------------------------------------------------------------


def authorized_emails() -> set[str]:
    """The set of Google accounts allowed to open the dashboard.

    Always includes the account that connected Gmail (the owner). Plus any
    addresses in ``dashboard_authorized_emails`` — the future-accounts seam.
    """
    emails: set[str] = set()

    stored = load_token()
    if stored is not None and stored.account_email:
        emails.add(stored.account_email.strip().lower())

    emails.update(get_settings().dashboard_authorized_email_list)
    return emails


def is_authorized(email: str | None) -> bool:
    """True if ``email`` is allowed to access the dashboard."""
    if not email:
        return False
    return email.strip().lower() in authorized_emails()


# --------------------------------------------------------------------
# Sessions — signed cookies
# --------------------------------------------------------------------


def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().session_secret, salt=_SESSION_SALT
    )


def _session_max_age_seconds() -> int:
    return get_settings().dashboard_session_max_age_hours * 3600


def issue_session(email: str) -> str:
    """Return a signed session token for ``email`` to set as a cookie."""
    return _session_serializer().dumps({"email": email.strip().lower()})


def read_session(token: str | None) -> str | None:
    """Return the email from a valid, unexpired session token, else ``None``."""
    if not token:
        return None
    try:
        data = _session_serializer().loads(
            token, max_age=_session_max_age_seconds()
        )
    except SignatureExpired:
        log.info("dashboard_session_expired")
        return None
    except BadSignature:
        log.warning("dashboard_session_bad_signature")
        return None
    email = data.get("email") if isinstance(data, dict) else None
    return email or None


def current_user(request: Any) -> str | None:
    """Return the signed-in, still-authorized email for a request, or ``None``.

    Re-checks authorization on every request, not just at sign-in: if the owner
    disconnects Gmail or the allowlist changes, an old cookie stops working
    immediately.
    """
    email = read_session(request.cookies.get(SESSION_COOKIE))
    if email is None or not is_authorized(email):
        return None
    return email


# --------------------------------------------------------------------
# Google Sign-In round-trip
# --------------------------------------------------------------------


def _login_state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().session_secret, salt=_STATE_SALT
    )


def _client_config() -> dict[str, Any]:
    s = get_settings()
    if not s.google_client_id or not s.google_client_secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set to enable "
            "dashboard sign-in (see .env.example)."
        )
    return {
        "web": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [s.dashboard_login_redirect_uri],
        }
    }


def _allow_insecure_transport_for_local_dev(redirect_uri: str, app_env: str) -> None:
    """Let oauthlib accept a plain-``http://`` loopback redirect URI.

    Same fix as :func:`app.gmail.oauth._allow_insecure_transport_for_local_dev`
    — ``google-auth-oauthlib`` refuses to exchange a code unless the redirect
    URI is HTTPS, even for ``localhost``. Only relaxed for a non-production app
    running against ``http://``; Render/production always uses HTTPS.
    """
    if app_env != "production" and redirect_uri.startswith("http://"):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


def _login_flow():
    from google_auth_oauthlib.flow import Flow

    s = get_settings()
    _allow_insecure_transport_for_local_dev(s.dashboard_login_redirect_uri, s.app_env)
    return Flow.from_client_config(
        client_config=_client_config(),
        scopes=list(LOGIN_SCOPES),
        redirect_uri=s.dashboard_login_redirect_uri,
        # build_login_url() and complete_login() each build their own fresh
        # Flow object (a separate request from the one that started the sign-
        # in), so there's no way to hand this one the code_verifier the
        # library would auto-generate for PKCE — that only ever lived on the
        # throwaway object from build_login_url(). PKCE protects public
        # clients that can't hold a secret; this app already authenticates
        # with client_secret, so it isn't needed here. Same fix as
        # app.gmail.oauth._flow() (Phase 11 found this the hard way against a
        # live mailbox — see docs/TECHNICAL_STATUS.md's Phase 11 bug list).
        autogenerate_code_verifier=False,
    )


def build_login_url() -> tuple[str, str]:
    """Return ``(url, signed_state)`` to start "Sign in with Google"."""
    nonce = secrets.token_urlsafe(24)
    signed_state = _login_state_serializer().dumps({"n": nonce})
    url, _ = _login_flow().authorization_url(
        access_type="online",
        include_granted_scopes="false",
        # "consent" (matching app.gmail.oauth's proven-working flow) forces
        # Google to show a real consent screen every time. "select_account"
        # lets Google silently re-continue an already-fresh session instead
        # (the callback comes back with prompt=none when it does) — and that
        # silent path is what was producing "(invalid_grant) Missing code
        # verifier": a mismatch between what this app's Flow object expects
        # and whatever Google's own mediation layer did on its side.
        prompt="consent",
        state=signed_state,
    )
    return url, signed_state


def verify_login_state(state: str) -> bool:
    try:
        _login_state_serializer().loads(state, max_age=_STATE_MAX_AGE_SECONDS)
    except BadSignature:
        log.warning("dashboard_login_state_invalid")
        return False
    return True


def complete_login(code: str, state: str) -> str:
    """Exchange the sign-in ``code`` for the verified Google email address.

    Raises ``PermissionError`` if the ``state`` can't be verified. This is the
    only function here that talks to Google; tests replace it wholesale.
    """
    if not verify_login_state(state):
        raise PermissionError("Sign-in state validation failed.")

    flow = _login_flow()
    flow.fetch_token(code=code)

    from googleapiclient.discovery import build

    service = build(
        "oauth2", "v2", credentials=flow.credentials, cache_discovery=False
    )
    info = service.userinfo().get().execute()
    email = (info.get("email") or "").strip().lower()
    if not email:
        raise RuntimeError("Google did not return an email for the sign-in.")
    return email


__all__ = (
    "LOGIN_SCOPES",
    "SESSION_COOKIE",
    "authorized_emails",
    "build_login_url",
    "complete_login",
    "current_user",
    "is_authorized",
    "issue_session",
    "read_session",
    "verify_login_state",
)
