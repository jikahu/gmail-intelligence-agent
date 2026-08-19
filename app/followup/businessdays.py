"""Business-day arithmetic with US + Kenya public holidays (CLAUDE.md §3, §10).

Follow-up timers run on **business days**, not calendar days: three business
days means three days that aren't a weekend and aren't a public holiday in
either the US or Kenya. This user's world spans both (an America/New_York
digest, Kenya holidays), so both calendars count.

**Holidays are computed, never listed.** CLAUDE.md §3 is explicit: "compute US
+ Kenya public holidays programmatically; never hard-code year lists." So the
fixed-date holidays are generated per year, the floating ones (third Monday of
January, last Monday of May, …) are derived, and the Easter-based Kenyan
holidays come from the Anonymous Gregorian algorithm. Nothing here is a
year-by-year table that would rot.

Observance rules are included because a day off shifts the working calendar:
US federal holidays on a Saturday are observed the Friday before and on a
Sunday the Monday after; Kenyan holidays on a Sunday are observed the following
Monday (cascading if that Monday is itself a holiday).

Known limitation: **Kenya's Islamic holidays** (Idd-ul-Fitr, Idd-ul-Azha)
follow the lunar calendar and are declared by proclamation, so they are not
computed. The only effect is that a timer spanning one of those two days a year
could fire a day early. Documented, not hidden.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

#: The follow-up window used throughout Phase 7 (CLAUDE.md §10).
FOLLOWUP_BUSINESS_DAYS = 3


# --------------------------------------------------------------------
# Calendar primitives
# --------------------------------------------------------------------


def _easter(year: int) -> date:
    """Easter Sunday via the Anonymous Gregorian (Meeus/Jones/Butcher) algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th (1-based) ``weekday`` of a month. Monday = 0."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` of a month."""
    next_month = date(year + (month == 12), (month % 12) + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _us_observed(holiday: date) -> date:
    """Federal observance: Saturday → the Friday before, Sunday → the Monday after."""
    if holiday.weekday() == 5:  # Saturday
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:  # Sunday
        return holiday + timedelta(days=1)
    return holiday


# --------------------------------------------------------------------
# Holiday sets
# --------------------------------------------------------------------


def _us_raw(year: int) -> set[date]:
    """US federal holidays generated for ``year`` (observed dates)."""
    days = {
        _us_observed(date(year, 1, 1)),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),  # MLK Jr. Day — 3rd Monday of January
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day — 3rd Monday of February
        _last_weekday(year, 5, 0),  # Memorial Day — last Monday of May
        _us_observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day — 1st Monday of September
        _nth_weekday(year, 10, 0, 2),  # Columbus Day — 2nd Monday of October
        _us_observed(date(year, 11, 11)),  # Veterans Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving — 4th Thursday of November
        _us_observed(date(year, 12, 25)),  # Christmas Day
    }
    if year >= 2021:
        days.add(_us_observed(date(year, 6, 19)))  # Juneteenth (federal since 2021)
    return days


def _observe_sunday(days: set[date]) -> set[date]:
    """Kenyan rule: a Sunday holiday is observed the next non-holiday weekday."""
    result = set(days)
    for holiday in sorted(days):
        if holiday.weekday() == 6:  # Sunday
            substitute = holiday + timedelta(days=1)
            while substitute in result:  # cascade past a collision (Christmas/Boxing)
                substitute += timedelta(days=1)
            result.add(substitute)
    return result


def _kenya_raw(year: int) -> set[date]:
    """Kenyan public holidays generated for ``year`` (Islamic holidays excluded)."""
    easter = _easter(year)
    fixed = {
        date(year, 1, 1),  # New Year's Day
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Easter Monday
        date(year, 5, 1),  # Labour Day
        date(year, 6, 1),  # Madaraka Day
        date(year, 10, 10),  # Huduma Day
        date(year, 10, 20),  # Mashujaa Day
        date(year, 12, 12),  # Jamhuri Day
        date(year, 12, 25),  # Christmas Day
        date(year, 12, 26),  # Boxing Day / Utamaduni Day
    }
    return _observe_sunday(fixed)


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    """All US + Kenya public holidays *observed in* ``year``.

    Generated from ``year-1 … year+1`` and filtered to the target year, so an
    observance that spills across a year boundary (a New Year's Day that lands
    on a Saturday is observed the previous 31 December) is attributed correctly.
    """
    everything: set[date] = set()
    for candidate_year in (year - 1, year, year + 1):
        everything |= _us_raw(candidate_year)
        everything |= _kenya_raw(candidate_year)
    return frozenset(day for day in everything if day.year == year)


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------


def is_holiday(day: date) -> bool:
    return day in holidays(day.year)


def is_business_day(day: date) -> bool:
    """A weekday (Mon–Fri) that is not a US or Kenya public holiday."""
    return day.weekday() < 5 and not is_holiday(day)


def add_business_days(start: date, n: int) -> date:
    """The date ``n`` business days after ``start`` (``start`` itself not counted).

    ``n == 0`` returns ``start`` unchanged. Weekends and holidays are skipped,
    so adding 3 business days to a Friday lands on the following Wednesday.
    """
    if n <= 0:
        return start
    current = start
    remaining = n
    while remaining > 0:
        current += timedelta(days=1)
        if is_business_day(current):
            remaining -= 1
    return current


def business_days_between(start: date, end: date) -> int:
    """Business days in the half-open interval ``(start, end]``.

    Counts working days strictly after ``start`` up to and including ``end`` —
    i.e. "how many business days have passed since ``start`` as of ``end``".
    Returns 0 when ``end <= start``.
    """
    if end <= start:
        return 0
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if is_business_day(current):
            count += 1
    return count


def is_overdue_by(start: date, today: date, threshold: int = FOLLOWUP_BUSINESS_DAYS) -> bool:
    """True when at least ``threshold`` business days have passed since ``start``."""
    return business_days_between(start, today) >= threshold


__all__ = (
    "FOLLOWUP_BUSINESS_DAYS",
    "add_business_days",
    "business_days_between",
    "holidays",
    "is_business_day",
    "is_holiday",
    "is_overdue_by",
)
