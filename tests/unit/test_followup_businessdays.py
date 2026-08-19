"""Business-day + holiday calendar (Phase 7)."""

from __future__ import annotations

from datetime import date

import pytest

from app.followup import businessdays as bd


# -------- US federal holidays (2026, verified) --------


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 1, 1),   # New Year's Day (Thu)
        date(2026, 1, 19),  # MLK Jr. Day — 3rd Monday of January
        date(2026, 2, 16),  # Presidents' Day — 3rd Monday of February
        date(2026, 5, 25),  # Memorial Day — last Monday of May
        date(2026, 6, 19),  # Juneteenth (Fri)
        date(2026, 7, 3),   # Independence Day observed (Jul 4 is a Saturday)
        date(2026, 9, 7),   # Labor Day — 1st Monday of September
        date(2026, 10, 12), # Columbus Day — 2nd Monday of October
        date(2026, 11, 11), # Veterans Day (Wed)
        date(2026, 11, 26), # Thanksgiving — 4th Thursday of November
        date(2026, 12, 25), # Christmas Day (Fri)
    ],
)
def test_us_federal_holidays_2026(day: date) -> None:
    assert bd.is_holiday(day)
    assert not bd.is_business_day(day)


def test_independence_day_shifts_off_the_weekend() -> None:
    # Jul 4 2026 is a Saturday; the observed holiday is Friday the 3rd.
    assert bd.is_holiday(date(2026, 7, 3))
    assert not bd.is_holiday(date(2026, 7, 4))  # the Saturday itself is just a weekend


def test_new_year_observance_spills_into_the_prior_year() -> None:
    # Jan 1 2022 is a Saturday → observed Friday 31 Dec 2021.
    assert bd.is_holiday(date(2021, 12, 31))


# -------- Kenya holidays --------


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 5, 1),   # Labour Day
        date(2026, 6, 1),   # Madaraka Day
        date(2026, 10, 20), # Mashujaa Day
        date(2026, 12, 12), # Jamhuri Day
        date(2026, 12, 26), # Boxing Day / Utamaduni Day
        date(2026, 4, 3),   # Good Friday (Easter is 5 Apr 2026)
        date(2026, 4, 6),   # Easter Monday
    ],
)
def test_kenya_holidays_2026(day: date) -> None:
    assert bd.is_holiday(day)


def test_kenya_sunday_holiday_is_observed_on_monday() -> None:
    # Jamhuri Day 12 Dec 2021 was a Sunday → the Monday is observed.
    assert bd.is_holiday(date(2021, 12, 13))


# -------- business-day arithmetic --------


def test_add_business_days_skips_the_weekend() -> None:
    # Friday 14 Aug 2026 + 3 business days = Wednesday 19 Aug.
    assert bd.add_business_days(date(2026, 8, 14), 3) == date(2026, 8, 19)


def test_add_business_days_skips_a_holiday() -> None:
    # Thursday 2 Jul 2026 + 3 business days skips Fri 3 Jul (Independence
    # observed) and the weekend → Wednesday 8 Jul.
    assert bd.add_business_days(date(2026, 7, 2), 3) == date(2026, 7, 8)


def test_add_zero_business_days_is_identity() -> None:
    assert bd.add_business_days(date(2026, 8, 17), 0) == date(2026, 8, 17)


def test_business_days_between_counts_the_open_interval() -> None:
    # Monday → Thursday is three business days (Tue, Wed, Thu).
    assert bd.business_days_between(date(2026, 8, 17), date(2026, 8, 20)) == 3


def test_business_days_between_is_zero_backwards() -> None:
    assert bd.business_days_between(date(2026, 8, 20), date(2026, 8, 17)) == 0


def test_is_overdue_by_threshold() -> None:
    sent = date(2026, 8, 17)  # Monday
    assert not bd.is_overdue_by(sent, date(2026, 8, 19))  # only 2 business days
    assert bd.is_overdue_by(sent, date(2026, 8, 20))      # 3 business days


# -------- the "computed, never listed" property --------


def test_holidays_are_generated_for_any_year() -> None:
    """No hard-coded year table: every year yields a full, plausible set."""
    for year in range(2018, 2036):
        days = bd.holidays(year)
        # Christmas is always present (as itself or its observed date).
        assert any(d.month == 12 and d.day in (24, 25, 26, 27) for d in days)
        # A January holiday is always present (New Year and/or MLK).
        assert any(d.month == 1 for d in days)
        # A sane number of distinct holidays.
        assert 12 <= len(days) <= 30


def test_weekend_is_never_a_business_day() -> None:
    assert not bd.is_business_day(date(2026, 8, 15))  # Saturday
    assert not bd.is_business_day(date(2026, 8, 16))  # Sunday
    assert bd.is_business_day(date(2026, 8, 18))      # ordinary Tuesday
