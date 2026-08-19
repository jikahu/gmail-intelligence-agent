"""Waiting for Reply + Overdue Action, and the clear-on-reply behaviour (Phase 7)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.followup.threads import evaluate_thread, expects_reply
from tests.fixtures.emails import DEFAULT_USER, make_message

TODAY = date(2026, 8, 20)  # Thursday


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 12, tzinfo=timezone.utc)


def _sent(subject: str, snippet: str, when: datetime, **kw) -> object:
    kw.setdefault("to", ["bob@example.com"])
    msg = make_message(
        sender=DEFAULT_USER,
        subject=subject,
        snippet=snippet,
        sent_by_user=True,
        **kw,
    )
    msg.date = when
    return msg


def _incoming(subject: str, snippet: str, when: datetime, **kw) -> object:
    kw.setdefault("to", [DEFAULT_USER])
    msg = make_message(
        sender="bob@example.com",
        subject=subject,
        snippet=snippet,
        **kw,
    )
    msg.date = when
    return msg


# -------- expects_reply --------


def test_question_expects_a_reply() -> None:
    msg = _sent("Quick one", "Can you review this by Friday?", _dt(2026, 8, 17))
    assert expects_reply(msg) is True


def test_a_thank_you_does_not_expect_a_reply() -> None:
    msg = _sent("Re: docs", "Thanks!", _dt(2026, 8, 17))
    assert expects_reply(msg) is False


def test_a_message_with_no_recipient_does_not_expect_a_reply() -> None:
    msg = _sent("Note to self", "Remember to file this?", _dt(2026, 8, 17), to=[])
    assert expects_reply(msg) is False


def test_a_broadcast_does_not_expect_a_reply() -> None:
    msg = _sent(
        "Announcement",
        "What do you all think?",
        _dt(2026, 8, 17),
        to=[f"p{i}@example.com" for i in range(15)],
    )
    assert expects_reply(msg) is False


# -------- Waiting for Reply --------


def test_waiting_for_reply_after_three_business_days() -> None:
    msg = _sent("Proposal", "Could you let me know your thoughts?", _dt(2026, 8, 17))
    waiting, overdue = evaluate_thread([msg], TODAY)
    assert len(waiting) == 1
    assert waiting[0].business_days_elapsed == 3
    assert waiting[0].proposed_label == "AI/Waiting-For-Reply"
    assert overdue == []


def test_not_waiting_before_three_business_days() -> None:
    # Sent Tuesday, only 2 business days have passed by Thursday.
    msg = _sent("Proposal", "Could you confirm?", _dt(2026, 8, 18))
    waiting, _ = evaluate_thread([msg], TODAY)
    assert waiting == []


def test_reply_clears_waiting_for_reply() -> None:
    asked = _sent("Question", "What do you think?", _dt(2026, 8, 17), thread_id="t1")
    replied = _incoming("Re: Question", "Here are my thoughts.", _dt(2026, 8, 19), thread_id="t1")
    waiting, _ = evaluate_thread([asked, replied], TODAY)
    assert waiting == []  # the other party replied — nothing to chase


# -------- Overdue Action --------


def test_overdue_action_after_three_business_days() -> None:
    msg = _incoming(
        "Action required", "Please sign and return.", _dt(2026, 8, 17), message_id="i1"
    )
    _waiting, overdue = evaluate_thread([msg], TODAY, action_required={"i1": True})
    assert len(overdue) == 1
    assert overdue[0].business_days_elapsed == 3
    assert overdue[0].proposed_label == "AI/Action-Required"


def test_no_overdue_action_when_engine_did_not_flag_it() -> None:
    msg = _incoming("FYI newsletter", "Just an update.", _dt(2026, 8, 17), message_id="i1")
    _waiting, overdue = evaluate_thread([msg], TODAY, action_required={"i1": False})
    assert overdue == []


def test_user_reply_clears_overdue_action() -> None:
    ask = _incoming(
        "Action required", "Please sign.", _dt(2026, 8, 17), message_id="i1", thread_id="t2"
    )
    reply = _sent("Re: Action required", "Done, signed and returned.", _dt(2026, 8, 19), thread_id="t2")
    _waiting, overdue = evaluate_thread([ask, reply], TODAY, action_required={"i1": True})
    assert overdue == []  # the user responded — no longer overdue


def test_thread_without_dates_produces_nothing() -> None:
    msg = make_message(sender=DEFAULT_USER, subject="No date", sent_by_user=True)
    assert evaluate_thread([msg], TODAY) == ([], [])
