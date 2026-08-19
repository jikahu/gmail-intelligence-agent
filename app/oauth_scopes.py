"""Combined, currently-active OAuth scope set.

This is the single source of truth for "what does the app currently ask Google
for". Every phase that adds a new scope registers it in its domain-specific
scopes module (e.g. ``app.gmail.scopes``, ``app.sheets.scopes``) and this file
aggregates them into ``ACTIVE_SCOPES``.

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
from app.sheets import scopes as sheets_scopes


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return tuple(seen)


#: All scopes the app currently requests, in a stable, deduped order.
ACTIVE_SCOPES: tuple[str, ...] = _dedupe(
    (
        *gmail_scopes.PHASE_1_SCOPES,
        *sheets_scopes.PHASE_2_SCOPES,
        *gmail_scopes.PHASE_11_SCOPES,
    )
)

#: Merged description dict.
SCOPE_DESCRIPTIONS: Mapping[str, str] = {
    **gmail_scopes.SCOPE_DESCRIPTIONS,
    **sheets_scopes.SCOPE_DESCRIPTIONS,
}


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
