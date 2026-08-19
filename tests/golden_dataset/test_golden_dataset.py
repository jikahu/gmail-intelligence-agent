"""The golden dataset regression test (CLAUDE.md §15).

The permanent, automated half of the launch quality gate: this dataset has
known-correct answers, so it runs on every ``pytest`` pass — unlike the live
250-email acceptance run, which needs a connected mailbox and is therefore
user-triggered (see ``app/acceptance/``).
"""

from __future__ import annotations

from app.acceptance.golden import evaluate
from tests.golden_dataset.dataset import GOLDEN_CONTEXT, GOLDEN_EXAMPLES

#: The categories CLAUDE.md §15 names for the 250-email acceptance sample.
_NAMED_CATEGORIES = frozenset(
    {
        "financial", "security", "government", "personal", "work", "career",
        "receipts", "purchases", "travel", "educational", "substack",
        "other_newsletters", "promotions", "automated_notifications",
        "cold_outreach", "attachments", "active_threads", "suspicious",
    }
)


def test_golden_dataset_has_zero_protected_false_reviews() -> None:
    """The single most important number (CLAUDE.md §15): must always be 0."""
    report = evaluate(GOLDEN_EXAMPLES, context=GOLDEN_CONTEXT)
    assert report.protected_false_reviews == 0, report.as_dict()


def test_golden_dataset_matches_every_hand_labeled_expectation() -> None:
    report = evaluate(GOLDEN_EXAMPLES, context=GOLDEN_CONTEXT)
    failures = [
        {
            "category": f.example.category,
            "note": f.example.expected.note,
            "mismatches": list(f.mismatches),
        }
        for f in report.failures
    ]
    assert failures == []
    assert report.accuracy == 1.0


def test_golden_dataset_covers_every_claude_md_15_category() -> None:
    covered = {example.category for example in GOLDEN_EXAMPLES}
    missing = _NAMED_CATEGORIES - covered
    assert not missing, f"golden dataset is missing categories: {sorted(missing)}"


def test_golden_dataset_has_at_least_one_negative_case() -> None:
    """The gate can't be satisfied by simply never reviewing anything."""
    report = evaluate(GOLDEN_EXAMPLES, context=GOLDEN_CONTEXT)
    reviewed = sum(1 for ex in GOLDEN_EXAMPLES if ex.expected.expect_review is True)
    assert reviewed >= 5
    assert report.total == len(GOLDEN_EXAMPLES)
