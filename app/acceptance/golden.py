"""Golden-dataset support (CLAUDE.md §15).

> Golden dataset support: examples with known correct classifications, used
> to compare classifier versions.

Unlike the live 250-email acceptance run (which needs a connected mailbox and
therefore can only be run by the user), a golden dataset is a small,
hand-labeled, checked-in fixture set — so it can run in every ``pytest`` pass
and act as a permanent regression test. The actual dataset content lives in
``tests/golden_dataset/`` (a *test* fixture, per CLAUDE.md §4's repo layout);
this module only defines the shapes and the comparison logic, so it's safe
for the app package to import even though nothing here ever imports from
``tests/``.

Metrics computed match CLAUDE.md §15's list: overall accuracy, a per-category
breakdown, and — the one that matters most — the protected-email false-Review
rate, which must be zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoldenExpectation:
    """What a golden example's classification *should* look like.

    Every field is optional — ``None`` (or an empty set) means "don't check
    this," so an example can assert only what it's actually meant to prove.
    """

    expected_labels: frozenset[str] = frozenset()
    expected_priority: str | None = None  # "P1" / "P2" / "P3"
    expect_review: bool | None = None
    expect_protected: bool | None = None
    note: str = ""


@dataclass(frozen=True)
class GoldenExample:
    """One hand-labeled fixture: a message plus its known-correct outcome."""

    message: object  # app.classification.message.EmailMessage
    expected: GoldenExpectation
    category: str


@dataclass(frozen=True)
class GoldenCaseResult:
    example: GoldenExample
    actual: object  # app.classification.engine.Classification
    matched: bool
    mismatches: tuple[str, ...]


@dataclass
class GoldenReport:
    total: int
    matched: int
    protected_examples: int
    protected_false_reviews: int
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    failures: list[GoldenCaseResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return (self.matched / self.total) if self.total else 0.0

    @property
    def protected_false_review_rate(self) -> float:
        return (
            self.protected_false_reviews / self.protected_examples
            if self.protected_examples
            else 0.0
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "matched": self.matched,
            "accuracy": self.accuracy,
            "protected_examples": self.protected_examples,
            "protected_false_reviews": self.protected_false_reviews,
            "protected_false_review_rate": self.protected_false_review_rate,
            "by_category": self.by_category,
            "failures": [
                {
                    "category": f.example.category,
                    "note": f.example.expected.note,
                    "mismatches": list(f.mismatches),
                }
                for f in self.failures
            ],
        }


def _compare(expected: GoldenExpectation, actual) -> list[str]:
    mismatches: list[str] = []

    if expected.expected_labels:
        actual_values = {label.value for label in actual.labels}
        missing = expected.expected_labels - actual_values
        if missing:
            mismatches.append(f"missing labels: {sorted(missing)}")

    if expected.expected_priority is not None and actual.priority.value != expected.expected_priority:
        mismatches.append(
            f"priority {actual.priority.value!r} != expected {expected.expected_priority!r}"
        )

    if expected.expect_review is not None and actual.review != expected.expect_review:
        mismatches.append(f"review={actual.review} != expected {expected.expect_review}")

    if expected.expect_protected is not None and actual.protected != expected.expect_protected:
        mismatches.append(
            f"protected={actual.protected} != expected {expected.expect_protected}"
        )

    return mismatches


def evaluate(examples: list[GoldenExample], context=None) -> GoldenReport:
    """Classify every example and score it against its expectation.

    ``context`` defaults to an empty :class:`ClassificationContext` — golden
    examples are meant to be self-contained (no VIPs/rules assumed) unless a
    test explicitly builds one.
    """
    from app.classification.context import ClassificationContext
    from app.classification.engine import classify

    context = context if context is not None else ClassificationContext()

    results: list[GoldenCaseResult] = []
    for example in examples:
        actual = classify(example.message, context)
        mismatches = _compare(example.expected, actual)
        results.append(
            GoldenCaseResult(
                example=example, actual=actual, matched=not mismatches, mismatches=tuple(mismatches)
            )
        )

    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = by_category.setdefault(result.example.category, {"total": 0, "matched": 0})
        bucket["total"] += 1
        bucket["matched"] += int(result.matched)

    # "Protected" for this metric means the example asserts the message must
    # never be reviewed — either explicitly (expect_review=False) or via
    # expect_protected=True. That mirrors what the live acceptance run checks:
    # not "was `protected` set," but "should this have been safe from Review."
    protected_results = [
        r
        for r in results
        if r.example.expected.expect_review is False or r.example.expected.expect_protected is True
    ]
    protected_false_reviews = [r for r in protected_results if r.actual.review]

    return GoldenReport(
        total=len(results),
        matched=sum(r.matched for r in results),
        protected_examples=len(protected_results),
        protected_false_reviews=len(protected_false_reviews),
        by_category=by_category,
        failures=[r for r in results if not r.matched],
    )


__all__ = (
    "GoldenCaseResult",
    "GoldenExample",
    "GoldenExpectation",
    "GoldenReport",
    "evaluate",
)
