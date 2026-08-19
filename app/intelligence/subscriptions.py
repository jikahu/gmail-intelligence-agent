"""Recognising subscriptions and recurring charges (CLAUDE.md §10).

The agent's job here is narrow and stops well short of acting: spot that a
message is about a subscription, pull out the service, the amount, how often
it bills, and when it next renews. It may mark a subscription
``suggested_review`` — a hint for the dashboard — but it never cancels
anything and never marks one for review silently on the user's behalf
(CLAUDE.md §20 forbids auto-cancellation entirely).
"""

from __future__ import annotations

import re
from datetime import date

from app.classification import patterns
from app.classification.message import EmailMessage
from app.intelligence import dates, money
from app.intelligence.models import Subscription
from app.intelligence.senders import brand_name

_FREQUENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bper\s+year\b|\bannually\b|\byearly\b|/\s?yr\b|/\s?year\b|a\s+year\b", "annually"),
    (r"\bper\s+month\b|\bmonthly\b|/\s?mo\b|/\s?month\b|a\s+month\b", "monthly"),
    (r"\bper\s+week\b|\bweekly\b|/\s?wk\b|/\s?week\b", "weekly"),
    (r"\bquarterly\b|\bper\s+quarter\b|every\s+3\s+months\b", "quarterly"),
)
_FREQUENCY_RE = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _FREQUENCY_PATTERNS)

_RENEWAL_CUE_RE = re.compile(
    r"renews?(?:\s+on)?|renewal|auto-?renew|next\s+billing|will\s+be\s+charged|"
    r"expires?(?:\s+on)?|trial\s+ends?|billed\s+on",
    re.IGNORECASE,
)

#: Wording that marks a moment worth a second look — a trial about to convert,
#: or a price going up. These become ``suggested_review``.
_REVIEW_CUE_RE = re.compile(
    r"trial\s+end|trial\s+expir|free\s+trial|will\s+be\s+charged|"
    r"price\s+increase|new\s+price|rate\s+change|renew",
    re.IGNORECASE,
)

def _billing_frequency(text: str) -> str:
    for regex, label in _FREQUENCY_RE:
        if regex.search(text):
            return label
    return "unknown"


def _renewal_date(text: str, reference: date) -> str | None:
    """The date sitting closest to renewal/billing wording, if any."""
    cues = [m.span() for m in _RENEWAL_CUE_RE.finditer(text)]
    if not cues:
        return None
    best: str | None = None
    best_gap = 10**9
    for extracted in dates.extract_dates(text, reference):
        for cstart, cend in cues:
            gap = min(abs(extracted.start - cend), abs(cstart - extracted.end))
            if gap < best_gap:
                best_gap, best = gap, extracted.iso
    # Only trust it when the date actually sits near a cue.
    return best if best_gap <= 60 else None


def _looks_like_subscription(message: EmailMessage) -> bool:
    headline = message.subject_and_snippet
    if patterns.SUBSCRIPTION.matches(headline):
        return True
    body = message.searchable_text
    return bool(
        patterns.SUBSCRIPTION.matches(body)
        and any(regex.search(body) for regex, _ in _FREQUENCY_RE)
    )


def extract_subscription(message: EmailMessage, today: date) -> Subscription | None:
    """Return a :class:`Subscription` if this email is about one, else ``None``."""
    if not _looks_like_subscription(message):
        return None

    reference = message.date.date() if message.date else today
    text = message.searchable_text

    primary = money.primary_money(text)
    review = "suggested_review" if _REVIEW_CUE_RE.search(text) else ""

    return Subscription(
        service=brand_name(message, fallback="Unknown service"),
        sender_domain=message.sender_registrable_domain or message.sender_domain,
        amount=primary.amount if primary else None,
        currency=primary.currency if primary else None,
        billing_frequency=_billing_frequency(text),
        renewal_date=_renewal_date(text, reference),
        review_status=review,
        message_id=message.message_id,
        thread_id=message.thread_id,
    )


__all__ = ("extract_subscription",)
