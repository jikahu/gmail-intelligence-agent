"""Google OAuth scopes requested by the app.

Every scope is listed here with a plain-English justification so that the
user-facing consent dialog can never contain a permission we don't document.

Phase 1 (read-only Gmail + Contacts) is intentionally the *minimum* set. Later
phases must not silently add scopes; they must add them here and re-request
consent from the user.
"""

from __future__ import annotations

from typing import Mapping

#: Sign the user in and let us verify their identity + email address.
OPENID_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)

#: Read Gmail messages, threads, labels, headers, bodies, attachments.
#: This scope does NOT grant modify, send, or delete.
GMAIL_READ_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
)

#: Read Google Contacts and "Other contacts" (frequent correspondents).
#: Used by relationship-protection rules in CLAUDE.md §8.
CONTACTS_READ_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
)

#: The exact set of scopes Phase 1 requests, in a stable order.
PHASE_1_SCOPES: tuple[str, ...] = (
    *OPENID_SCOPES,
    *GMAIL_READ_SCOPES,
    *CONTACTS_READ_SCOPES,
)

#: Add/remove Gmail labels, archive, and Trash (recoverable — Gmail keeps
#: Trash for 30 days). Deliberately NOT ``gmail.send``, ``gmail.compose``,
#: ``gmail.insert``, or the full ``mail.google.com`` scope — this app never
#: sends mail, and there is no API call this scope grants that permanently
#: deletes anything (CLAUDE.md §5: never auto-delete or auto-Trash).
GMAIL_WRITE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.modify",
)

#: The additional scope Phase 11 requests, on top of Phase 1's read grant.
#: A user who connected before Phase 11 is missing this and must reconnect
#: (``app.gmail.tokens.missing_scopes`` / the dashboard's "reconnect
#: required" banner already handle prompting for that).
PHASE_11_SCOPES: tuple[str, ...] = (*GMAIL_WRITE_SCOPES,)

#: Set/change a label's color (the taxonomy label color-coding feature).
#: Confirmed empirically, not from the docs: Gmail's discovery document lists
#: ``gmail.modify`` as an accepted scope for ``labels.create``/``labels.patch``,
#: but in practice a token with only ``gmail.modify`` gets a real 403
#: "insufficient authentication scopes" the moment a ``color`` field is
#: included in either call -- Google enforces label color specifically behind
#: this narrower scope.
#:
#: NOT currently included in ``app.oauth_scopes.ACTIVE_SCOPES`` -- requesting
#: it in production caused Google to reject every token refresh with a real
#: ``invalid_scope`` error (not just label-color calls, every Gmail call),
#: because this scope was never added to this OAuth client's consent screen
#: in Google Cloud Console (APIs & Services > OAuth consent screen > Scopes).
#: That's a manual console step, not something this codebase can do. Add this
#: back to ``ACTIVE_SCOPES`` only after that's done and a reconnect has been
#: verified to keep refreshing cleanly (watch for ``RefreshError`` in logs).
GMAIL_LABEL_COLOR_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.labels",
)

#: Human-readable descriptions for each scope, surfaced in the dashboard and
#: the plain-English docs so the user always knows what they granted.
SCOPE_DESCRIPTIONS: Mapping[str, str] = {
    "openid": "Sign you in.",
    "https://www.googleapis.com/auth/userinfo.email": "See your Google account email address.",
    "https://www.googleapis.com/auth/gmail.readonly": "Read your Gmail messages and settings. Does NOT allow sending, modifying, or deleting.",
    "https://www.googleapis.com/auth/contacts.readonly": "Read your Google Contacts.",
    "https://www.googleapis.com/auth/contacts.other.readonly": "Read your 'Other contacts' (people you email but haven't added to Contacts).",
    "https://www.googleapis.com/auth/gmail.modify": (
        "Add or remove Gmail labels, archive a message, and move a message to "
        "Gmail's own Trash (recoverable for 30 days). Does NOT allow sending "
        "mail or permanently deleting anything."
    ),
    "https://www.googleapis.com/auth/gmail.labels": (
        "Set the color of the taxonomy labels this app creates, so they're "
        "visually distinct in your inbox. Does not grant reading, sending, "
        "or deleting messages."
    ),
}


def describe(scopes: tuple[str, ...] = PHASE_1_SCOPES) -> list[tuple[str, str]]:
    """Return ``(scope, description)`` pairs suitable for UI display."""
    return [(s, SCOPE_DESCRIPTIONS.get(s, "(no description)")) for s in scopes]


__all__ = (
    "OPENID_SCOPES",
    "GMAIL_READ_SCOPES",
    "CONTACTS_READ_SCOPES",
    "GMAIL_WRITE_SCOPES",
    "GMAIL_LABEL_COLOR_SCOPES",
    "PHASE_1_SCOPES",
    "PHASE_11_SCOPES",
    "SCOPE_DESCRIPTIONS",
    "describe",
)
