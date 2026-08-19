"""The audit trail (Phase 9, CLAUDE.md §12/§13).

Phase 9 still makes zero Gmail writes, so the central thing to prove is that
the audit trail never lies about that: ``labels_before``/``labels_after`` and
``inbox_before``/``inbox_after`` must always match for both event shapes, and
``reversible`` must always be false until Phase 11 gives the app something
that could actually be undone.
"""

from __future__ import annotations

from app.audit import service as audit_service
from app.audit.models import ACTOR_AGENT, ACTOR_USER, safe_subject_ref
from app.classification.engine import classify
from app.classification.pipeline import PreviewResult
from tests.fixtures.emails import bulk_headers, make_message


def _result(**kwargs) -> PreviewResult:
    message = make_message(**kwargs)
    return PreviewResult(message=message, classification=classify(message))


# --------------------------------------------------------------------
# event_from_result — classification-run proposals
# --------------------------------------------------------------------


def test_event_from_result_before_and_after_match_in_dry_run() -> None:
    result = _result(
        sender="alerts@bank.com",
        subject="Action needed: your payment failed",
        body="Your payment failed and your card was declined.",
    )
    event = audit_service.event_from_result(result, run_id="run-1")

    assert event.labels_before == event.labels_after
    assert event.inbox_before == event.inbox_after
    assert event.reversible is False
    assert "dry run" in event.action_taken.lower()
    assert event.undo_status == "not_applicable (dry run)"
    assert event.actor == ACTOR_AGENT


def test_event_from_result_carries_the_proposed_decision() -> None:
    result = _result(
        sender="alerts@bank.com",
        subject="Action needed: your payment failed",
        body="Your payment failed and your card was declined.",
    )
    event = audit_service.event_from_result(result, run_id="run-2")

    assert event.run_id == "run-2"
    assert event.gmail_message_id == result.message.message_id
    assert event.priority == "P1"
    assert event.confidence
    assert event.ai_reason_summary == result.classification.rationale
    assert event.rules_triggered  # "payment failed" fires a P1 rule


def test_event_from_result_reflects_a_review_decision() -> None:
    result = _result(
        sender="deals@shop.example",
        subject="50% off everything — limited time!",
        body="Huge sale. Unsubscribe here.",
        headers=bulk_headers(),
    )
    event = audit_service.event_from_result(result, run_id="run-2b")

    assert "AI/Review" in event.classification


def test_event_from_result_subject_is_truncated_and_safe() -> None:
    long_subject = "x" * 500
    result = _result(sender="a@b.com", subject=long_subject, body="hello")
    event = audit_service.event_from_result(result, run_id="run-3")

    assert len(event.subject_safe_ref) <= 120
    assert event.subject_safe_ref == safe_subject_ref(long_subject)


def test_event_ids_are_unique_per_event() -> None:
    result = _result(sender="a@b.com", subject="hi", body="hi")
    e1 = audit_service.event_from_result(result, run_id="run-4")
    e2 = audit_service.event_from_result(result, run_id="run-4")
    assert e1.event_id != e2.event_id


# --------------------------------------------------------------------
# event_from_action — dashboard clicks
# --------------------------------------------------------------------


def test_event_from_action_is_never_reversible_in_phase_9() -> None:
    event = audit_service.event_from_action(
        message_id="m1",
        thread_id="t1",
        subject="A newsletter",
        classification="AI/Review",
        priority="P3",
        confidence=0.6,
        reason="bulk mailing",
        action_taken="Kept",
    )
    assert event.actor == ACTOR_USER
    assert event.reversible is False
    assert event.labels_before == event.labels_after == "AI/Review"
    assert event.action_taken == "Kept"


# --------------------------------------------------------------------
# record_run — one row per message, sharing a run_id
# --------------------------------------------------------------------


class _FakeAuditLog:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, values) -> None:  # noqa: ANN001
        self.rows.append(dict(values))

    def record_many(self, rows) -> None:  # noqa: ANN001
        self.rows.extend(dict(values) for values in rows)


class _FakeWorkbook:
    def __init__(self) -> None:
        self.audit_log = _FakeAuditLog()


def test_record_run_writes_one_row_per_message_sharing_a_run_id() -> None:
    workbook = _FakeWorkbook()
    results = [
        _result(message_id="a", sender="x@y.com", subject="hi", body="hi"),
        _result(message_id="b", sender="x@y.com", subject="there", body="there"),
    ]

    run_id = audit_service.record_run(workbook, results)

    assert len(workbook.audit_log.rows) == 2
    assert {row["run_id"] for row in workbook.audit_log.rows} == {run_id}
    assert {row["gmail_message_id"] for row in workbook.audit_log.rows} == {"a", "b"}


def test_record_run_accepts_an_explicit_run_id() -> None:
    workbook = _FakeWorkbook()
    results = [_result(message_id="a", sender="x@y.com", subject="hi", body="hi")]

    run_id = audit_service.record_run(workbook, results, run_id="explicit-run")

    assert run_id == "explicit-run"
    assert workbook.audit_log.rows[0]["run_id"] == "explicit-run"
