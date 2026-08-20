"""Match a message against a Gmail label the user already made by hand.

Plenty of Gmail users already have folders like "Uber" or "Amazon" from years
of manual filing. Rather than fight that with a second, parallel taxonomy,
the agent recognizes those existing labels and routes matching mail into them
too -- a receipt from Uber gets both ``Purchases-Receipts`` (what kind of
email this is) and the user's own ``Uber`` label (where they already keep
that kind of thing), additively. Nothing is ever removed from an existing
label, and this never creates a new one -- it only recognizes labels that are
already there.

Deterministic and cheap (string matching against a label list already fetched
for :meth:`~app.gmail.write_client.GmailWriteClient.ensure_labels`), so it
runs before AI is ever considered (CLAUDE.md §3: rules first).
"""

from __future__ import annotations

import re

from app.classification.labels import Label
from app.classification.message import EmailMessage, registrable_domain

#: Never match one of the agent's own taxonomy labels as if it were a
#: user-made folder -- that would just be a very roundabout no-op.
_TAXONOMY_LEAVES: frozenset[str] = frozenset(label.value.lower() for label in Label)

#: Gmail's own system labels, plus categories -- never candidates either.
_SYSTEM_LEAVES: frozenset[str] = frozenset(
    {
        "inbox", "sent", "draft", "spam", "trash", "unread", "starred",
        "important", "chat", "category_personal", "category_social",
        "category_promotions", "category_updates", "category_forums",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _domain_root(domain: str) -> str:
    """``uber.com`` -> ``uber``. Empty if the domain is too short to trust."""
    root = registrable_domain(domain).split(".")[0]
    return root if len(root) >= 3 else ""


def _sender_name_words(sender_name: str) -> set[str]:
    return {w for w in _WORD_RE.findall(sender_name.lower()) if len(w) >= 3}


def _leaf(label_name: str) -> str:
    """Gmail nests labels with ``/`` (``Travel/Uber``) -- match on the part
    the user actually named after their vendor, not the parent path."""
    return label_name.rsplit("/", 1)[-1].strip().lower()


def match_existing_label(
    existing_label_names: set[str], message: EmailMessage
) -> str | None:
    """Return the existing Gmail label name to also apply, or ``None``.

    Only ever returns a label already present in ``existing_label_names`` --
    this never proposes creating one. Domain match is checked first (higher
    precision: "uber.com" mail matching a label literally named "Uber" is a
    very safe bet); sender display-name words are a fallback for the same
    reason a receipt's From header is usually the vendor's own name.
    """
    domain_root = _domain_root(message.sender_registrable_domain)
    name_words = _sender_name_words(message.sender_name)

    candidates = {leaf: name for name in existing_label_names if (leaf := _leaf(name))}
    candidates = {
        leaf: name
        for leaf, name in candidates.items()
        if leaf not in _TAXONOMY_LEAVES and leaf not in _SYSTEM_LEAVES
    }

    if domain_root and domain_root in candidates:
        return candidates[domain_root]
    for word in name_words:
        if word in candidates:
            return candidates[word]
    return None


__all__ = ("match_existing_label",)
