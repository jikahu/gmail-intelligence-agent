"""Extracting the minimum financial detail from an email (CLAUDE.md §7).

"Store the minimum needed": amount, currency, due date, the kind of money
event, and a *safe* account reference — the last four digits, never more. This
module reads a financial email and returns exactly that much, or nothing.
"""

from __future__ import annotations

import re
from datetime import date

from app.classification import patterns
from app.classification.message import EmailMessage
from app.intelligence import dates, money
from app.intelligence.models import FinancialDetail

_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("refund", r"refund|reimbursement|money\s+back|credited\s+back"),
    ("statement", r"statement|e-?statement|monthly\s+statement"),
    ("bill", r"invoice|bill\b|amount\s+due|balance\s+due|payment\s+due"),
    ("payment", r"payment\s+(?:received|confirmed|successful|made)|"
                r"we\s+received\s+your\s+payment|thank\s+you\s+for\s+your\s+payment"),
    ("charge", r"charged|transaction|purchase|debited|withdrawal|charge\b"),
)
_KIND_RE = tuple((label, re.compile(p, re.IGNORECASE)) for label, p in _KIND_PATTERNS)

_DUE_CUE_RE = re.compile(
    r"due(?:\s+by|\s+date|\s+on)?|pay\s+by|payable\s+by|before", re.IGNORECASE
)


def _kind(text: str) -> str:
    for label, regex in _KIND_RE:
        if regex.search(text):
            return label
    return "unknown"


def _due_date(text: str, reference: date) -> str | None:
    cues = [m.span() for m in _DUE_CUE_RE.finditer(text)]
    if not cues:
        return None
    best: str | None = None
    best_gap = 10**9
    for extracted in dates.extract_dates(text, reference):
        for cstart, cend in cues:
            gap = min(abs(extracted.start - cend), abs(cstart - extracted.end))
            if gap < best_gap:
                best_gap, best = gap, extracted.iso
    return best if best_gap <= 45 else None


def extract_financial(message: EmailMessage, today: date) -> FinancialDetail | None:
    """Return a :class:`FinancialDetail` for a money email, else ``None``."""
    headline = message.subject_and_snippet
    text = message.searchable_text
    if not (patterns.FINANCIAL.matches(headline) or patterns.FINANCIAL.matches(text)):
        return None

    reference = message.date.date() if message.date else today
    primary = money.primary_money(text)
    refs = money.extract_account_refs(text)

    detail = FinancialDetail(
        kind=_kind(text),
        amount=primary.amount if primary else None,
        currency=primary.currency if primary else None,
        due_date=_due_date(text, reference),
        account_ref=refs[0].last4 if refs else None,
        original_text=(primary.original_text if primary else ""),
    )
    # Nothing concrete extracted (no money, no ref, no due date, unknown kind)
    # isn't worth a row — the classification already labelled it Financial.
    if (
        detail.amount is None
        and detail.account_ref is None
        and detail.due_date is None
        and detail.kind == "unknown"
    ):
        return None
    return detail


__all__ = ("extract_financial",)
