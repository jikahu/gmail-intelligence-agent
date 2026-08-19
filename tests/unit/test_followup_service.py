"""Deadline status refinement and the follow-up orchestration (Phase 7)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.followup import service
from app.followup.deadlines import deadline_status
from app.intelligence import analyze
from app.intelligence.models import Deadline, IntelligenceReport, MessageIntelligence
from tests.fixtures.emails import DEFAULT_USER, make_message

TODAY = date(2026, 8, 20)  # Thursday


def _deadline(due: date, category: str = "payment", action: bool = True) -> Deadline:
    return Deadline(
        message_id="m",
        thread_id="t",
        normalized_date=due,
        original_text=due.isoformat(),
        confidence=0.9,
        category=category,
        action_required=action,
        status="upcoming",
        label="Payment due",
    )


# -------- status refinement --------


def test_status_due_today_is_due_soon() -> None:
    assert deadline_status(TODAY, TODAY) == "due_soon"


def test_status_within_three_business_days_is_due_soon() -> None:
    assert deadline_status(date(2026, 8, 25), TODAY) == "due_soon"  # Mon: 3 biz days


def test_status_beyond_three_business_days_is_upcoming() -> None:
    assert deadline_status(date(2026, 8, 27), TODAY) == "upcoming"  # 5 biz days out


def test_status_past_date_is_overdue() -> None:
    assert deadline_status(date(2026, 8, 18), TODAY) == "overdue"


# -------- evaluate --------


def test_evaluate_splits_due_soon_and_overdue() -> None:
    report = service.evaluate(
        messages=[],
        deadlines=[_deadline(date(2026, 8, 21)), _deadline(date(2026, 8, 10))],
        today=TODAY,
    )
    assert len(report.due_soon) == 1
    assert len(report.overdue_deadlines) == 1


def test_informational_renewal_is_not_chased() -> None:
    report = service.evaluate(
        messages=[],
        deadlines=[_deadline(date(2026, 8, 21), category="renewal", action=False)],
        today=TODAY,
    )
    assert report.due_soon == []
    assert report.overdue_deadlines == []


def test_evaluate_finds_waiting_and_overdue_action() -> None:
    sent = make_message(
        sender=DEFAULT_USER,
        to=["bob@example.com"],
        subject="Proposal",
        snippet="Can you review this?",
        sent_by_user=True,
        message_id="s1",
        thread_id="ta",
    )
    sent.date = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)

    incoming = make_message(
        sender="vendor@example.com",
        to=[DEFAULT_USER],
        subject="Action required",
        snippet="Please sign.",
        message_id="i1",
        thread_id="tb",
    )
    incoming.date = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)

    report = service.evaluate(
        messages=[sent, incoming],
        deadlines=[],
        today=TODAY,
        action_required={"i1": True},
    )
    assert len(report.waiting_for_reply) == 1
    assert len(report.overdue_actions) == 1


# -------- refine_report --------


def test_refine_report_sharpens_deadline_status() -> None:
    intel = MessageIntelligence(
        message_id="m1",
        thread_id="t1",
        deadlines=[_deadline(date(2026, 8, 21))],  # tomorrow-ish, status "upcoming"
    )
    report = IntelligenceReport(messages={"m1": intel})

    service.refine_report(report, TODAY)

    assert report.messages["m1"].deadlines[0].status == "due_soon"


def test_refine_report_over_real_extraction() -> None:
    msg = make_message(
        subject="Invoice",
        body="Payment due tomorrow.",
        message_id="m1",
        thread_id="t1",
    )
    report = analyze([msg], today=TODAY)
    service.refine_report(report, TODAY)
    statuses = [d.status for d in report.messages["m1"].deadlines]
    assert "due_soon" in statuses
