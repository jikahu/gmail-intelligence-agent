"""Date extraction and normalization (Phase 6)."""

from __future__ import annotations

from datetime import date

from app.intelligence.dates import extract_dates, first_date

REF = date(2026, 8, 17)  # a Monday


def _iso(text: str) -> str | None:
    found = first_date(text, REF)
    return found.iso if found else None


def test_iso_date() -> None:
    assert _iso("renews on 2026-09-15") == "2026-09-15"


def test_us_month_day_year() -> None:
    assert _iso("respond by 9/15/2026") == "2026-09-15"


def test_month_name_with_year() -> None:
    assert _iso("due September 15, 2026") == "2026-09-15"


def test_day_month_with_year() -> None:
    assert _iso("due 15 September 2026") == "2026-09-15"


def test_month_name_without_year_infers_the_coming_one() -> None:
    found = first_date("September 15", REF)
    assert found is not None
    assert found.iso == "2026-09-15"
    assert found.year_was_inferred is True


def test_past_month_day_rolls_to_next_year() -> None:
    # March 1 has already gone by on 2026-08-17, so it means 2027.
    assert _iso("renew before March 1") == "2027-03-01"


def test_tomorrow_and_today() -> None:
    assert _iso("must submit today") == "2026-08-17"
    assert _iso("due tomorrow") == "2026-08-18"


def test_within_n_days() -> None:
    assert _iso("please reply within 5 days") == "2026-08-22"


def test_ambiguous_numeric_is_flagged_and_low_confidence() -> None:
    found = first_date("effective 03/04/2026", REF)
    assert found is not None
    assert found.is_ambiguous is True
    assert found.confidence <= 0.65
    # Defaults to US month/day.
    assert found.iso == "2026-03-04"


def test_unambiguous_day_first_when_first_number_over_12() -> None:
    found = first_date("on 25/12/2026", REF)
    assert found is not None
    assert found.iso == "2026-12-25"
    assert found.is_ambiguous is False


def test_full_date_wins_over_its_month_day_submatch() -> None:
    found = first_date("meet on September 15, 2026 please", REF)
    assert found is not None
    assert found.iso == "2026-09-15"
    assert found.year_was_inferred is False  # the year was written, not inferred


def test_ordinals_are_handled() -> None:
    assert _iso("by September 15th") == "2026-09-15"


def test_impossible_date_is_ignored() -> None:
    assert first_date("13/45/2026", REF) is None


def test_plain_prose_has_no_date() -> None:
    assert first_date("thanks so much, talk soon", REF) is None


def test_multiple_dates_are_returned_without_overlap() -> None:
    found = extract_dates(
        "starts September 10, 2026 and ends September 14, 2026", REF
    )
    assert [d.iso for d in found] == ["2026-09-10", "2026-09-14"]
