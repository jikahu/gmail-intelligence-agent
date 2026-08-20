"""Combined, currently-active OAuth scope set.

This is the single source of truth for "what does the app currently ask Google
for". :mod:`app.gmail.scopes` registers every scope this app requests, and
this file re-exports the currently-active subset as ``ACTIVE_SCOPES``.

The rules in CLAUDE.md §16 require:

1. Scopes are documented (see ``describe()`` — one row per scope, description shown to the user).
2. New scopes are never added silently — a test asserts that ``ACTIVE_SCOPES``
   equals the deterministic sum of registered phase scopes.
3. Adding a new scope forces the user to re-consent
   (:func:`app.gmail.tokens.missing_scopes` detects this at boot and the
   dashboard surfaces a "reconnect required" state).
"""

from __future__ import annotations

from typing import Mapping

from app.gmail import scopes as gmail_scopes


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return tuple(seen)


#: All scopes the app currently requests, in a stable, deduped order.
#:
#: GMAIL_LABEL_COLOR_SCOPES (gmail.labels) is deliberately NOT included here.
#: Requesting it broke live token refresh in production with a real Google
#: 'invalid_scope' error on every single refresh -- not just label-color
#: calls, *everything* Gmail-related, because google-auth includes the full
#: requested scope list on every refresh-token grant request, and Google
#: rejects the whole request if any scope in it isn't actually registered on
#: this OAuth client's consent screen. gmail.labels was never added there (a
#: manual Google Cloud Console step, not something this codebase controls) --
#: so until that's done and confirmed working, this scope must stay out of
#: ACTIVE_SCOPES. See app/gmail/scopes.py's own docstring on the constant.
ACTIVE_SCOPES: tuple[str, ...] = _dedupe(
    (
        *gmail_scopes.PHASE_1_SCOPES,
        *gmail_scopes.PHASE_11_SCOPES,
    )
)

#: Merged description dict.
SCOPE_DESCRIPTIONS: Mapping[str, str] = {**gmail_scopes.SCOPE_DESCRIPTIONS}


def describe(scopes: tuple[str, ...] = ACTIVE_SCOPES) -> list[tuple[str, str]]:
    """Return ``(scope, description)`` pairs suitable for UI display."""
    return [(s, SCOPE_DESCRIPTIONS.get(s, "(no description)")) for s in scopes]


def missing_from(granted: tuple[str, ...] | list[str] | set[str] | None) -> list[str]:
    """Return the scopes in ACTIVE_SCOPES that are not present in ``granted``.

    Google occasionally returns scopes in a slightly different form (e.g.
    duplicates or adjacent aliases). We compare as sets to be robust.
    """
    granted_set = set(granted or [])
    return [s for s in ACTIVE_SCOPES if s not in granted_set]


__all__ = (
    "ACTIVE_SCOPES",
    "SCOPE_DESCRIPTIONS",
    "describe",
    "missing_from",
)
