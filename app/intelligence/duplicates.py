"""Near-duplicate detection (CLAUDE.md §9).

Mass senders often fire the same message twice, or send near-identical
campaigns days apart. Spotting that is a *Review-confidence* signal — a second
copy of something already low-value is a little more clearly low-value. It is
**never** a reason to delete anything, and it never overrides protection: two
copies of a bank statement are two statements, both kept.

This module only reports the groups. The engine's protection veto still runs
untouched; nothing here can hide an email.
"""

from __future__ import annotations

import hashlib
import re

from app.classification.message import EmailMessage
from app.intelligence.models import DuplicateGroup

#: How alike two messages' word-sets must be to count as near-duplicates.
_JACCARD_THRESHOLD = 0.85
#: Fewer than this many meaningful words and we don't try — too little to tell.
_MIN_TOKENS = 3

_URL_RE = re.compile(r"https?://\S+")
_NON_ALPHA_RE = re.compile(r"[^a-z\s]+")


def _tokens(message: EmailMessage) -> set[str]:
    text = f"{message.subject} {message.snippet}".lower()
    text = _URL_RE.sub(" ", text)
    text = _NON_ALPHA_RE.sub(" ", text)  # drop digits and punctuation
    return {word for word in text.split() if len(word) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


def find_duplicates(messages: list[EmailMessage]) -> list[DuplicateGroup]:
    """Group near-identical messages. Returns only groups of two or more.

    Two messages are grouped when they come from the **same sender domain** and
    their word-sets overlap past :data:`_JACCARD_THRESHOLD`. Requiring the same
    domain keeps unrelated senders who happen to share stock phrases apart.
    """
    indexed = [(i, msg) for i, msg in enumerate(messages)]
    token_sets = {i: _tokens(msg) for i, msg in indexed}

    uf = _UnionFind(len(messages))
    for pos_a in range(len(indexed)):
        idx_a, msg_a = indexed[pos_a]
        tokens_a = token_sets[idx_a]
        if len(tokens_a) < _MIN_TOKENS:
            continue
        for pos_b in range(pos_a + 1, len(indexed)):
            idx_b, msg_b = indexed[pos_b]
            if msg_a.sender_domain != msg_b.sender_domain or not msg_a.sender_domain:
                continue
            tokens_b = token_sets[idx_b]
            if len(tokens_b) < _MIN_TOKENS:
                continue
            if _jaccard(tokens_a, tokens_b) >= _JACCARD_THRESHOLD:
                uf.union(idx_a, idx_b)

    # Collect members per component, preserving original order.
    components: dict[int, list[EmailMessage]] = {}
    for idx, msg in indexed:
        components.setdefault(uf.find(idx), []).append(msg)

    groups: list[DuplicateGroup] = []
    for members in components.values():
        if len(members) < 2:
            continue
        ids = tuple(m.message_id for m in members)
        fingerprint = hashlib.sha1(
            "|".join(sorted(ids)).encode("utf-8")
        ).hexdigest()[:12]
        groups.append(
            DuplicateGroup(
                fingerprint=fingerprint,
                message_ids=ids,
                representative_subject=members[0].subject,
            )
        )
    return groups


def duplicate_message_ids(groups: list[DuplicateGroup]) -> set[str]:
    """The 'extra' copies — every group member except the first (the original)."""
    extras: set[str] = set()
    for group in groups:
        extras.update(group.message_ids[1:])
    return extras


__all__ = ("find_duplicates", "duplicate_message_ids")
