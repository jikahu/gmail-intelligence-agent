"""Encrypted-at-rest storage for the Google OAuth refresh token.

Phase 1 stores tokens in a single file at ``oauth_tokens/token.json.enc``.
The file is encrypted with Fernet using a symmetric key derived from
``SESSION_SECRET`` in the environment (via SHA-256 → urlsafe base64).

Trade-offs:
- Simple, works on a personal Windows machine with zero extra setup.
- The path is in ``.gitignore``. The plaintext refresh token never touches disk
  and is never committed to git.
- The local file alone is **not durable on a disposable host** — a fresh
  GitHub Actions runner starts with no filesystem state at all, every single
  run. Phase 16 addresses this: if ``GOOGLE_OAUTH_SEED_REFRESH_TOKEN`` is set
  (a GitHub Actions repo secret, unlike the runner's local disk, *does*
  survive between runs), :func:`load_token` rebuilds the local file from it
  automatically when the file is missing — see ``_seed_from_env`` below and
  ``docs/plain-english/PHASE_16_RENDER_DEPLOYMENT.md`` (the mechanism was
  originally built for Render's ephemeral filesystem; it works identically
  for a GitHub Actions runner's).
- Rotating ``SESSION_SECRET`` invalidates the stored token; the user will have
  to re-consent. This is intentional.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

TOKEN_DIR: Path = Path("oauth_tokens")
TOKEN_FILE: Path = TOKEN_DIR / "token.json.enc"


@dataclass
class StoredToken:
    """The subset of an OAuth token response we persist."""

    refresh_token: str
    access_token: str | None = None
    expiry_iso: str | None = None
    scopes: list[str] = field(default_factory=list)
    account_email: str | None = None
    token_uri: str = "https://oauth2.googleapis.com/token"
    client_id: str | None = None
    # NOTE: client_secret is intentionally NOT stored on disk — it lives in
    # the environment (``GOOGLE_CLIENT_SECRET``) and is re-injected at refresh.

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "StoredToken":
        data: dict[str, Any] = json.loads(raw)
        return cls(**data)


def _fernet() -> Fernet:
    secret = get_settings().session_secret
    if not secret or secret == "change-me-to-a-long-random-string":
        raise RuntimeError(
            "SESSION_SECRET is unset or still the placeholder value. Generate a "
            "real one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _ensure_dir() -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)


def _restrict_permissions(path: Path) -> None:
    """Best-effort tighten of file permissions.

    On POSIX systems set mode 600. On Windows this is a no-op — access control
    is handled by the user's home directory ACL.
    """
    if os.name == "posix":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def save_token(token: StoredToken) -> Path:
    """Encrypt and write the token to disk. Returns the file path."""
    _ensure_dir()
    ciphertext = _fernet().encrypt(token.to_json().encode("utf-8"))
    TOKEN_FILE.write_bytes(ciphertext)
    _restrict_permissions(TOKEN_FILE)
    return TOKEN_FILE


def load_token() -> StoredToken | None:
    """Read the stored token, or ``None`` if not present / undecryptable.

    Undecryptable is treated the same as absent: the caller should prompt the
    user to reconnect Gmail rather than crash — *unless* a Phase 16 seed
    refresh token is configured, in which case a missing/undecryptable local
    file is rebuilt from the seed first (see ``_seed_from_env``).
    """
    if not TOKEN_FILE.exists():
        return _seed_from_env()
    try:
        plaintext = _fernet().decrypt(TOKEN_FILE.read_bytes())
    except InvalidToken:
        return _seed_from_env()
    return StoredToken.from_json(plaintext.decode("utf-8"))


def _seed_from_env() -> StoredToken | None:
    """Rebuild the local token file from ``GOOGLE_OAUTH_SEED_REFRESH_TOKEN``.

    Returns ``None`` (same as "no token at all") when no seed is configured —
    the normal case for local development, and before Gmail has ever been
    connected. When a seed *is* configured, this is what lets a brand new
    GitHub Actions runner reconnect Gmail on every single run with no
    persistent disk at all: the refresh token rarely changes once issued, so
    re-deriving the local file from a durably-stored repo secret on every run
    is enough. The access token is left unset — ``google-auth`` refreshes it
    automatically from the refresh token on the next API call.
    """
    settings = get_settings()
    seed = settings.google_oauth_seed_refresh_token
    if not seed:
        return None

    from app.oauth_scopes import ACTIVE_SCOPES  # local import to avoid cycle

    stored = StoredToken(
        refresh_token=seed,
        access_token=None,
        expiry_iso=None,
        scopes=list(ACTIVE_SCOPES),
        account_email=settings.google_oauth_seed_account_email,
        client_id=settings.google_client_id,
    )
    save_token(stored)
    return stored


def clear_token() -> bool:
    """Delete the stored token file. Returns True if a file was removed."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        return True
    return False


def token_exists() -> bool:
    return TOKEN_FILE.exists()


def missing_scopes() -> list[str]:
    """Return scopes in ACTIVE_SCOPES that are absent from the stored token.

    Empty list = fully authorized. Non-empty = user must re-consent so that the
    newly added scopes are granted. Called by the dashboard so we can surface
    a "reconnect required" state instead of failing on a live API call.
    """
    from app.oauth_scopes import missing_from  # local import to avoid cycle

    stored = load_token()
    if stored is None:
        return []
    return missing_from(stored.scopes)


__all__ = (
    "StoredToken",
    "TOKEN_DIR",
    "TOKEN_FILE",
    "save_token",
    "load_token",
    "clear_token",
    "token_exists",
    "missing_scopes",
)
