"""Orchestration for the follow-up pass (CLAUDE.md §10).

Pulls the two halves together — deadline timers and thread state — into one
:class:`FollowUpReport`. Pure and read-only: it reasons over already-fetched
messages and already-extracted deadlines, and returns findings. It changes no
classification and touches no mailbox.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping

from app.classification.message import EmailMessage
from app.followup import deadlines as deadlines_mod, threads as threads_mod
from app.followup.models import FollowUpReport
from app.intelligence.models import Deadline, IntelligenceReport


def _group_by_thread(messages: list[EmailMessage]) -> dict[str, list[EmailMessage]]:
    grouped: dict[str, list[EmailMessage]] = {}
    for message in messages:
        key = message.thread_id or message.message_id
        grouped.setdefault(key, []).append(message)
    return grouped


def evaluate(
    messages: list[EmailMessage],
    deadlines: list[Deadline],
    today: date,
    action_required: Mapping[str, bool] | None = None,
) -> FollowUpReport:
    """Compute the whole follow-up report for a run.

    ``messages`` are the messages in scope (grouped into threads here);
    ``deadlines`` are the Phase 6 deadlines; ``action_required`` maps a
    message id to whether the engine flagged it as needing action.
    """
    action_required = dict(action_required or {})

    due_soon, overdue_deadlines = deadlines_mod.followups(deadlines, today)

    waiting_for_reply = []
    overdue_actions = []
    for thread_messages in _group_by_thread(messages).values():
        waiting, overdue = threads_mod.evaluate_thread(
            thread_messages, today, action_required=action_required
        )
        waiting_for_reply.extend(waiting)
        overdue_actions.extend(overdue)

    return FollowUpReport(
        due_soon=due_soon,
        overdue_deadlines=overdue_deadlines,
        waiting_for_reply=waiting_for_reply,
        overdue_actions=overdue_actions,
    )


def refine_report(report: IntelligenceReport, today: date) -> IntelligenceReport:
    """Recompute every deadline's status with business-day logic, in place.

    Phase 6 filled the Deadlines with plain-calendar ``upcoming``/``overdue``;
    this upgrades them to ``due_soon`` where a business-day count warrants it, so
    a later persist writes the sharper status.
    """
    for intel in report.messages.values():
        intel.deadlines = [deadlines_mod.refine(d, today) for d in intel.deadlines]
    return report


def evaluate_from_results(results: list, report: IntelligenceReport, today: date) -> FollowUpReport:
    """Convenience wrapper over pipeline ``PreviewResult`` objects.

    Reads the messages and the engine's ``action_required`` flags off the
    results, and the deadlines off the intelligence report.
    """
    messages = [r.message for r in results]
    action_required = {
        r.message.message_id: bool(r.classification.action_required) for r in results
    }
    return evaluate(messages, report.all_deadlines(), today, action_required)


__all__ = ("evaluate", "evaluate_from_results", "refine_report")
