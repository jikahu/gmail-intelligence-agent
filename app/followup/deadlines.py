"""Refining a deadline's status with business-day logic (CLAUDE.md §10).

Phase 6 extracted deadlines and marked each simply ``upcoming`` or ``overdue``
on the plain calendar. Phase 7 sharpens that: a deadline within three business
days is **due soon**, one whose date has passed is **overdue**, everything else
stays **upcoming**. "Due soon" counts business days, so a Friday deadline seen
on Wednesday is due soon, but one seen the Monday ten days prior is not.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.followup import businessdays
from app.followup.models import FollowUpItem, FollowUpKind
from app.intelligence.models import Deadline


def deadline_status(due: date, today: date) -> str:
    """``overdue`` / ``due_soon`` / ``upcoming`` for a deadline date."""
    if due < today:
        return "overdue"
    if due == today:
        return "due_soon"  # due today is certainly soon
    if businessdays.business_days_between(today, due) <= businessdays.FOLLOWUP_BUSINESS_DAYS:
        return "due_soon"
    return "upcoming"


def refine(deadline: Deadline, today: date) -> Deadline:
    """Return a copy of ``deadline`` with its status recomputed for ``today``."""
    return replace(deadline, status=deadline_status(deadline.normalized_date, today))


def followups(deadlines: list[Deadline], today: date) -> tuple[list[FollowUpItem], list[FollowUpItem]]:
    """Turn deadlines into ``(due_soon, overdue)`` follow-up items.

    Only actionable deadlines raise a follow-up — a renewal you don't have to do
    anything about is recorded but isn't chased. Returns two lists so the report
    can rank overdue above due-soon.
    """
    due_soon: list[FollowUpItem] = []
    overdue: list[FollowUpItem] = []

    for deadline in deadlines:
        status = deadline_status(deadline.normalized_date, today)
        if status == "upcoming":
            continue
        if not deadline.action_required and deadline.category == "renewal":
            continue  # informational renewal — recorded, not chased

        item = FollowUpItem(
            kind=(
                FollowUpKind.OVERDUE_DEADLINE
                if status == "overdue"
                else FollowUpKind.DUE_SOON
            ),
            message_id=deadline.message_id,
            thread_id=deadline.thread_id,
            subject=deadline.label,
            reason=(
                f"{deadline.label} was due {deadline.iso}"
                if status == "overdue"
                else f"{deadline.label} is due {deadline.iso}"
            ),
            since=deadline.iso,
            due_date=deadline.iso,
        )
        (overdue if status == "overdue" else due_soon).append(item)

    return due_soon, overdue


__all__ = ("deadline_status", "followups", "refine")
