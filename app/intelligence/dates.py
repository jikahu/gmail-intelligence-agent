"""Deterministic date extraction and normalization (CLAUDE.md §10).

Phase 6 *reads* dates out of an email; it does not *reason* about them. The
three-business-day timers, the US and Kenya holiday calendar, and the "due
soon / overdue" refinement all belong to Phase 7. Here we only answer: what
calendar dates does this text mention, in ISO form, and how sure are we?

**Stdlib only, on purpose.** A dependency like ``dateutil`` would parse more
exotic strings, but email dates are overwhelmingly the dozen shapes below, and
CLAUDE.md §18 rule 10 says to avoid unnecessary libraries. Every date we return
carries the original wording and a confidence, so a caller can always see what
it matched and how firm the reading is.

Nothing here follows or executes anything found in a message — a date string is
data (CLAUDE.md §16).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

#: Never return more than this many dates from one text — a runaway or hostile
#: body full of numbers can't flood the dashboard or a later prompt.
MAX_DATES = 20

_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY_ALT = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"


@dataclass(frozen=True)
class ExtractedDate:
    """One calendar date found in a piece of text.

    ``value`` is the resolved date. ``original_text`` is the exact wording that
    produced it, so the dashboard can show *why* we think there's a date. The
    two flags below tell a caller how much to trust it.
    """

    value: date
    original_text: str
    confidence: float
    #: True when the text had no year and we inferred the nearest future one.
    year_was_inferred: bool = False
    #: True for a numeric date where day/month order was genuinely unclear.
    is_ambiguous: bool = False
    #: Character span in the source text; used to drop overlapping matches.
    start: int = 0
    end: int = 0

    @property
    def iso(self) -> str:
        return self.value.isoformat()


# --------------------------------------------------------------------
# Compiled patterns. Ordered longest / most-specific first so that, e.g.,
# "September 15, 2026" wins over the "September 15" sub-match.
# --------------------------------------------------------------------

_ISO_RE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_NUMERIC_RE = re.compile(r"(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})(?!\d)")
_MONTH_DAY_YEAR_RE = re.compile(
    rf"\b({_MONTH_ALT})\s+(\d{{1,2}}){_ORD},?\s+(\d{{4}})\b", re.IGNORECASE
)
_DAY_MONTH_YEAR_RE = re.compile(
    rf"\b(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_MONTH_ALT}),?\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_ALT})\s+(\d{{1,2}}){_ORD}\b", re.IGNORECASE
)
_DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_MONTH_ALT})\b", re.IGNORECASE
)
_TODAY_RE = re.compile(r"\b(today|tonight)\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_IN_N_DAYS_RE = re.compile(r"\b(?:in|within)\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_WEEKDAY_RE = re.compile(
    rf"\b(next\s+|this\s+|by\s+|on\s+)?({_WEEKDAY_ALT})\b", re.IGNORECASE
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _infer_year(month: int, day: int, reference: date) -> tuple[date | None, bool]:
    """Pick the nearest non-past year for a month/day with no year given.

    Deadlines and renewals point forward, so when a date has already gone by
    this year we read it as next year's. Returns ``(date, inferred)``.
    """
    this_year = _safe_date(reference.year, month, day)
    if this_year is None:
        # e.g. Feb 29 in a non-leap year — try the next leap-ish year.
        nxt = _safe_date(reference.year + 1, month, day)
        return nxt, True
    if this_year < reference:
        return _safe_date(reference.year + 1, month, day), True
    return this_year, True


def _normalize_2digit_year(raw: int) -> int:
    if raw >= 100:
        return raw
    return 2000 + raw if raw <= 68 else 1900 + raw


def _iter(text: str, reference: date):
    """Yield ``ExtractedDate`` candidates from every pattern (may overlap)."""

    for m in _ISO_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        value = _safe_date(y, mo, d)
        if value is not None:
            yield ExtractedDate(value, m.group(0), 0.97, start=m.start(), end=m.end())

    for m in _NUMERIC_RE.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), _normalize_2digit_year(int(m.group(3)))
        # a/b/year — decide day/month order.
        if a > 12 and b <= 12:  # day-first, unambiguous
            value, ambiguous, conf = _safe_date(y, b, a), False, 0.90
        elif b > 12 and a <= 12:  # US month-first, unambiguous
            value, ambiguous, conf = _safe_date(y, a, b), False, 0.90
        else:
            # Both <= 12: genuinely ambiguous. Default to US month/day (the
            # form most automated senders and America/New_York services use),
            # but flag it and lower confidence so nothing leans on the guess.
            value, ambiguous, conf = _safe_date(y, a, b), True, 0.60
        if value is not None:
            yield ExtractedDate(
                value, m.group(0), conf, is_ambiguous=ambiguous,
                start=m.start(), end=m.end(),
            )

    for m in _MONTH_DAY_YEAR_RE.finditer(text):
        mo, d, y = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
        value = _safe_date(y, mo, d)
        if value is not None:
            yield ExtractedDate(value, m.group(0), 0.95, start=m.start(), end=m.end())

    for m in _DAY_MONTH_YEAR_RE.finditer(text):
        d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
        value = _safe_date(y, mo, d)
        if value is not None:
            yield ExtractedDate(value, m.group(0), 0.95, start=m.start(), end=m.end())

    for m in _MONTH_DAY_RE.finditer(text):
        mo, d = _MONTHS[m.group(1).lower()], int(m.group(2))
        value, inferred = _infer_year(mo, d, reference)
        if value is not None:
            yield ExtractedDate(
                value, m.group(0), 0.75, year_was_inferred=inferred,
                start=m.start(), end=m.end(),
            )

    for m in _DAY_MONTH_RE.finditer(text):
        d, mo = int(m.group(1)), _MONTHS[m.group(2).lower()]
        value, inferred = _infer_year(mo, d, reference)
        if value is not None:
            yield ExtractedDate(
                value, m.group(0), 0.75, year_was_inferred=inferred,
                start=m.start(), end=m.end(),
            )

    for m in _TODAY_RE.finditer(text):
        yield ExtractedDate(reference, m.group(0), 0.85, start=m.start(), end=m.end())

    for m in _TOMORROW_RE.finditer(text):
        yield ExtractedDate(
            reference + timedelta(days=1), m.group(0), 0.85,
            start=m.start(), end=m.end(),
        )

    for m in _IN_N_DAYS_RE.finditer(text):
        days = int(m.group(1))
        if 0 <= days <= 366:
            yield ExtractedDate(
                reference + timedelta(days=days), m.group(0), 0.80,
                year_was_inferred=True, start=m.start(), end=m.end(),
            )

    for m in _WEEKDAY_RE.finditer(text):
        qualifier = (m.group(1) or "").strip().lower()
        target = _WEEKDAYS[m.group(2).lower()]
        delta = (target - reference.weekday()) % 7
        if qualifier == "next":
            delta = delta + 7 if delta else 7
        elif delta == 0:
            delta = 7  # a bare weekday name means the *next* one, not today
        yield ExtractedDate(
            reference + timedelta(days=delta), m.group(0), 0.55,
            year_was_inferred=True, start=m.start(), end=m.end(),
        )


def extract_dates(text: str, reference: date) -> list[ExtractedDate]:
    """Return the dates mentioned in ``text``, best first, no overlaps.

    ``reference`` anchors relative wording ("tomorrow") and year inference for
    dates written without one. Use the email's own send date, so "September 15"
    in an August email resolves the way the reader would have read it.
    """
    if not text:
        return []

    candidates = sorted(
        _iter(text, reference),
        key=lambda d: (d.start, -(d.end - d.start), -d.confidence),
    )

    accepted: list[ExtractedDate] = []
    occupied: list[tuple[int, int]] = []
    for cand in candidates:
        if any(cand.start < end and cand.end > start for start, end in occupied):
            continue
        accepted.append(cand)
        occupied.append((cand.start, cand.end))
        if len(accepted) >= MAX_DATES:
            break

    return accepted


def first_date(text: str, reference: date) -> ExtractedDate | None:
    found = extract_dates(text, reference)
    return found[0] if found else None


__all__ = ("ExtractedDate", "MAX_DATES", "extract_dates", "first_date")
