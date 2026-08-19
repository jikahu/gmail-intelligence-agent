"""Turning dates into deadlines (CLAUDE.md §10).

A date on its own isn't a deadline — "our September 15 webinar" is not
something you owe anyone. A deadline is a date sitting next to wording that
says the reader must *do* something by or on it: pay, reply, renew, attend,
register, interview. This module finds those pairings.

It only *extracts*. Whether a deadline is "due soon" or "overdue" in the
business-day sense — skipping weekends and US/Kenya holidays — is Phase 7. Here
we mark a date ``overdue`` only if it has already gone by on the plain
calendar, and ``upcoming`` otherwise.
"""

from __future__ import annotations

import re
from datetime import date

from app.classification.message import EmailMessage
from app.intelligence import dates
from app.intelligence.models import Deadline

#: How close (in characters) a cue word must sit to a date for the two to be
#: read as the same deadline.
_CUE_WINDOW = 45


class _Cue:
    __slots__ = ("regex", "category", "action_required", "label", "weight")

    def __init__(self, phrases, category, action_required, label, weight):
        self.regex = re.compile(
            "|".join(re.escape(p) for p in phrases), re.IGNORECASE
        )
        self.category = category
        self.action_required = action_required
        self.label = label
        self.weight = weight


#: Ordered by how strongly the wording implies "act by this date".
_CUES: tuple[_Cue, ...] = (
    _Cue(
        ("payment due", "amount due", "balance due", "bill due", "pay by",
         "payment is due", "due by", "please pay", "minimum payment"),
        "payment", True, "Payment due", 5,
    ),
    _Cue(
        ("respond by", "reply by", "rsvp by", "rsvp", "response required by",
         "please respond by", "get back to us by", "confirm by"),
        "response", True, "Respond by", 5,
    ),
    _Cue(
        ("register by", "registration closes", "enroll by", "enrol by",
         "apply by", "submit by", "application deadline", "sign up by",
         "last day to register", "deadline to"),
        "registration", True, "Registration deadline", 4,
    ),
    _Cue(
        ("interview", "phone screen", "screening call"),
        "interview", True, "Interview", 4,
    ),
    _Cue(
        ("appointment", "scheduled for", "your visit", "appointment on",
         "booked for", "see you on"),
        "appointment", True, "Appointment", 4,
    ),
    _Cue(
        ("renews on", "renewal date", "auto-renew", "will renew", "renews",
         "expires on", "expiration date", "valid until", "expires", "trial ends"),
        "renewal", False, "Renewal", 3,
    ),
    _Cue(
        ("estimated delivery", "expected delivery", "arriving", "arrives",
         "delivery date", "out for delivery", "will arrive"),
        "delivery", False, "Expected delivery", 2,
    ),
    _Cue(
        ("due date", "deadline", "due on", "no later than", "before"),
        "generic", True, "Deadline", 2,
    ),
)


def _reference(message: EmailMessage, today: date) -> date:
    return message.date.date() if message.date else today


def _nearest_cue(text: str, span: tuple[int, int]) -> _Cue | None:
    """Return the strongest cue whose wording sits near ``span``."""
    start, end = span
    best: _Cue | None = None
    for cue in _CUES:
        for m in cue.regex.finditer(text):
            gap = start - m.end() if m.end() <= start else m.start() - end
            if gap <= _CUE_WINDOW:
                if best is None or cue.weight > best.weight:
                    best = cue
                break
    return best


def extract_deadlines(
    message: EmailMessage,
    today: date,
    action_required: bool = False,
) -> list[Deadline]:
    """Find the actionable dates in one email.

    ``action_required`` is the rules engine's own read of the message. When it
    is true and the message names exactly one confident date with no explicit
    cue, we still record that date as a generic deadline — the engine already
    decided something is owed, we're just attaching the when.
    """
    text = f"{message.subject}\n{message.snippet}\n{message.body_text}".strip()
    if not text:
        return []

    reference = _reference(message, today)
    found = dates.extract_dates(text, reference)
    lowered = text.lower()

    deadlines: list[Deadline] = []
    for extracted in found:
        cue = _nearest_cue(lowered, (extracted.start, extracted.end))
        if cue is None:
            continue
        deadlines.append(
            _build(message, extracted, reference, cue.category,
                   cue.action_required, cue.label)
        )

    # Engine says "act on this" but no explicit cue matched, and there's a
    # single confident date — attach it rather than lose the deadline.
    if not deadlines and action_required:
        confident = [d for d in found if d.confidence >= 0.75]
        if len(confident) == 1:
            deadlines.append(
                _build(message, confident[0], reference, "generic", True, "Deadline")
            )

    return _dedupe(deadlines)


def _build(
    message: EmailMessage,
    extracted: dates.ExtractedDate,
    reference: date,
    category: str,
    action_required: bool,
    label: str,
) -> Deadline:
    status = "overdue" if extracted.value < reference else "upcoming"
    return Deadline(
        message_id=message.message_id,
        thread_id=message.thread_id,
        normalized_date=extracted.value,
        original_text=extracted.original_text,
        confidence=round(extracted.confidence, 3),
        category=category,
        action_required=action_required,
        status=status,
        label=label,
    )


def _dedupe(deadlines: list[Deadline]) -> list[Deadline]:
    """One deadline per calendar date — keep the most confident/actionable."""
    best: dict[date, Deadline] = {}
    for deadline in deadlines:
        current = best.get(deadline.normalized_date)
        if current is None or _rank(deadline) > _rank(current):
            best[deadline.normalized_date] = deadline
    return sorted(best.values(), key=lambda d: d.normalized_date)


def _rank(deadline: Deadline) -> tuple[int, float]:
    return (int(deadline.action_required), deadline.confidence)


__all__ = ("extract_deadlines",)
