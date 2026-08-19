"""Safety invariants for the intelligence layer (Phase 6).

The intelligence layer observes; it must never move mail. These tests pin that
down: it cannot change a classification, it cannot route a protected email to
Review (even a duplicated one), and the package stays decoupled from Gmail and
Sheets so it *can't* act even by accident.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date

from app.classification.engine import classify
from app.classification.pipeline import PreviewResult, build_intelligence
from app.intelligence import analyze
from tests.fixtures.emails import make_message

TODAY = date(2026, 8, 17)


def test_intelligence_does_not_mutate_the_classification() -> None:
    m = make_message(
        sender="statements@chase.com",
        subject="Your statement is ready",
        body="Amount due $50 by September 20, 2026.",
    )
    decision = classify(m)
    results = [PreviewResult(message=m, classification=decision)]

    build_intelligence(results, today=TODAY)

    # Same object, and none of its safety-relevant fields moved.
    assert results[0].classification is decision
    assert decision.review is False
    assert decision.keep_in_inbox is True
    assert results[0].intelligence is not None


def test_duplicate_protected_emails_are_still_kept() -> None:
    a = make_message(
        sender="statements@chase.com",
        subject="Your statement is ready to view today",
        body="Your balance is $100.",
        message_id="a",
        thread_id="ta",
    )
    b = make_message(
        sender="statements@chase.com",
        subject="Your statement is ready to view today",
        body="Your balance is $100.",
        message_id="b",
        thread_id="tb",
    )
    ca, cb = classify(a), classify(b)
    report = analyze([a, b], today=TODAY)

    # The duplicate pair is reported...
    assert report.batch.duplicate_groups
    # ...but both statements remain protected and kept, never Review.
    assert ca.protected and cb.protected
    assert not ca.review and not cb.review


def test_intelligence_package_imports_neither_gmail_nor_sheets() -> None:
    """Structural: the pure intelligence layer can't reach Gmail or Sheets.

    Persistence takes a workbook handle as an argument; it never imports the
    Sheets client itself. That keeps the whole package side-effect free by
    construction, not just by convention.
    """
    package = pathlib.Path(__file__).resolve().parents[2] / "app" / "intelligence"
    banned = ("app.gmail", "app.sheets", "googleapiclient")

    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if any(module == b or module.startswith(b + ".") for b in banned):
                    offenders.append(f"{path.name}: {module}")

    assert offenders == [], f"intelligence must stay pure, but found: {offenders}"


def test_persistence_writes_nothing_when_report_is_empty() -> None:
    from app.intelligence.models import IntelligenceReport

    class _Recorder:
        def __init__(self) -> None:
            self.calls = 0

        def upsert(self, values):  # noqa: ANN001
            self.calls += 1
            return "inserted"

    class _Workbook:
        def __init__(self) -> None:
            self.deadlines = _Recorder()
            self.subscriptions = _Recorder()
            self.trips = _Recorder()

    from app.intelligence import persistence

    workbook = _Workbook()
    persistence.persist(workbook, IntelligenceReport(), today=TODAY)

    assert workbook.deadlines.calls == 0
    assert workbook.subscriptions.calls == 0
    assert workbook.trips.calls == 0
