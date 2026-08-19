"""Material change detection (CLAUDE.md §10).

When a bank raises a fee, a SaaS raises its price, an insurer changes coverage,
or a service is discontinued, that's a *material change* — usually P2, and the
kind of thing that's easy to miss in a wall of boilerplate. The engine already
lifts these to P2; this module pulls out the specifics worth showing: what
changed, the old and new values when stated, and when it takes effect.

Nothing here acts. It reads the notice and summarises it.
"""

from __future__ import annotations

import re
from datetime import date

from app.classification import patterns
from app.classification.message import EmailMessage
from app.intelligence import dates
from app.intelligence.models import MaterialChange

_KINDS: tuple[tuple[str, str], ...] = (
    ("price", r"\bpric(?:e|es|ing|ed)\b|new\s+pric"),
    ("fee", r"\bfees?\b|service\s+charge|maintenance\s+charge"),
    ("interest_rate", r"interest\s+rate|\bapr\b|rate\s+change"),
    ("coverage", r"coverage|benefit\s+change|deductible"),
    ("service", r"discontinu|will\s+no\s+longer|end\s+of\s+life|shutting\s+down|"
                r"service\s+(?:ending|discontinued)"),
    ("terms", r"terms|policy\s+update|privacy\s+policy|agreement"),
)
_KIND_RE = tuple((label, re.compile(p, re.IGNORECASE)) for label, p in _KINDS)

# A monetary amount or a percentage — the two shapes a "from X to Y" carries.
# The number sub-pattern is strict (no trailing separators) so "$8," yields
# "$8", not "$8,".
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_VALUE = (
    rf"(?:[$€£¥₹₦]\s?(?:{_NUM})"
    rf"|(?:{_NUM})\s?%"
    rf"|(?:USD|EUR|GBP|KES|KSh|Ksh)\s?(?:{_NUM}))"
)
_FROM_TO_RE = re.compile(rf"from\s+({_VALUE})\s+to\s+({_VALUE})", re.IGNORECASE)
_TO_ONLY_RE = re.compile(
    rf"(?:increas\w*|chang\w*|adjust\w*|rais\w*|now|becomes?)\s+to\s+({_VALUE})",
    re.IGNORECASE,
)

_EFFECTIVE_CUE_RE = re.compile(
    r"effective(?:\s+date)?|starting|as\s+of|beginning|takes?\s+effect|"
    r"from\s+the",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"action\s+required|opt\s?-?\s?out|cancel\s+before|to\s+continue|"
    r"to\s+keep|if\s+you\s+(?:do\s+not|don't)\s+agree|please\s+review|"
    r"contact\s+us",
    re.IGNORECASE,
)


def _kind(text: str) -> str | None:
    for label, regex in _KIND_RE:
        if regex.search(text):
            return label
    return None


def _from_to(text: str) -> tuple[str | None, str | None]:
    m = _FROM_TO_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _TO_ONLY_RE.search(text)
    if m:
        return None, m.group(1).strip()
    return None, None


def _effective_date(text: str, reference: date) -> str | None:
    cues = [m.span() for m in _EFFECTIVE_CUE_RE.finditer(text)]
    if not cues:
        return None
    best: str | None = None
    best_gap = 10**9
    for extracted in dates.extract_dates(text, reference):
        for cstart, cend in cues:
            gap = min(abs(extracted.start - cend), abs(cstart - extracted.end))
            if gap < best_gap:
                best_gap, best = gap, extracted.iso
    return best if best_gap <= 60 else None


def _summary(kind: str, phrase: str, old: str | None, new: str | None) -> str:
    nice = {
        "price": "Price change",
        "fee": "Fee change",
        "interest_rate": "Interest rate change",
        "coverage": "Coverage change",
        "service": "Service change",
        "terms": "Terms update",
    }[kind]
    if old and new:
        return f"{nice}: from {old} to {new}"
    if new:
        return f"{nice}: now {new}"
    return f"{nice} ({phrase})" if phrase else nice


def extract_material_change(message: EmailMessage, today: date) -> MaterialChange | None:
    """Return a :class:`MaterialChange` if this email announces one, else ``None``."""
    headline = message.subject_and_snippet
    text = message.searchable_text
    phrase = patterns.MATERIAL_CHANGE.first_match(headline) or patterns.MATERIAL_CHANGE.first_match(text)
    if not phrase:
        return None

    kind = _kind(text) or "terms"
    reference = message.date.date() if message.date else today
    old, new = _from_to(text)

    return MaterialChange(
        kind=kind,
        summary=_summary(kind, phrase, old, new),
        old_value=old,
        new_value=new,
        effective_date=_effective_date(text, reference),
        action_required=bool(_ACTION_RE.search(text)),
        message_id=message.message_id,
        thread_id=message.thread_id,
    )


__all__ = ("extract_material_change",)
