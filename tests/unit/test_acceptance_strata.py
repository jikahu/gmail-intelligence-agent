"""The stratified-sample category buckets (Phase 10, CLAUDE.md §15)."""

from __future__ import annotations

from app.acceptance.strata import DEFAULT_SAMPLE_TARGET, STRATA


def test_strata_targets_sum_to_the_default_sample_target() -> None:
    assert sum(s.target for s in STRATA) == DEFAULT_SAMPLE_TARGET


def test_default_sample_target_is_250() -> None:
    assert DEFAULT_SAMPLE_TARGET == 250


def test_strata_names_are_unique() -> None:
    names = [s.name for s in STRATA]
    assert len(names) == len(set(names))


def test_every_stratum_has_a_positive_target() -> None:
    assert all(s.target > 0 for s in STRATA)


def test_every_claude_md_15_category_has_a_stratum() -> None:
    named = {
        "financial", "security", "government", "personal", "work", "career",
        "receipts", "purchases", "travel", "educational", "substack",
        "other_newsletters", "promotions", "automated_notifications",
        "cold_outreach", "attachments", "active_threads", "suspicious",
    }
    names = {s.name for s in STRATA}
    missing = named - names
    assert not missing, f"strata missing: {sorted(missing)}"
