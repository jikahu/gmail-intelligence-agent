"""Wires the seven Review-queue buttons (CLAUDE.md §13) to the learning,
audit, and (as of Phase 11) real Gmail-write layers.

Five actions (Keep, Review Correct, Make Sender Rule, Make Domain Rule,
Suggest VIP) only ever write to the control workbook. Restore to Inbox and
Trash now call real Gmail — both go through
:func:`app.gmail.apply.check_write_gate` first, so clicking either before
live writes are turned on produces a clear refusal, not a silent no-op.
Trash additionally requires the dashboard's separate confirmation page
(CLAUDE.md §5) before this module ever sees the request.
"""

from __future__ import annotations

from app.audit import service as audit_service
from app.gmail import dashboard_ops as gmail_dashboard_ops
from app.learning import service as learning_service
from app.learning.models import FeedbackOutcome

#: URL path segment -> plain-English label recorded on the audit row.
ACTION_LABELS: dict[str, str] = {
    "keep": "Kept",
    "restore": "Restored to Inbox",
    "review-correct": "Confirmed Review was correct",
    "make-sender-rule": "Suggested a sender rule",
    "make-domain-rule": "Suggested a domain rule",
    "suggest-vip": "Suggested as VIP",
    "trash": "Moved to Trash",
}

__all__ = ("ACTION_LABELS", "perform")


def perform(
    action: str,
    workbook,
    *,
    message_id: str,
    thread_id: str,
    sender_email: str,
    sender_name: str,
    subject: str,
    classification: str,
    reason: str,
) -> FeedbackOutcome:
    """Run one Review-queue action and record it. Raises ``KeyError`` if unknown."""
    if action not in ACTION_LABELS:
        raise KeyError(action)

    if action == "keep":
        outcome = learning_service.keep(
            workbook, message_id=message_id, thread_id=thread_id,
            classification=classification, reason=reason,
        )
    elif action == "review-correct":
        outcome = learning_service.review_correct(
            workbook, message_id=message_id, thread_id=thread_id,
            classification=classification, reason=reason,
        )
    elif action == "make-sender-rule":
        outcome = learning_service.make_sender_rule(
            workbook, message_id=message_id, thread_id=thread_id,
            sender_email=sender_email, subject=subject,
            classification=classification, reason=reason,
        )
    elif action == "make-domain-rule":
        outcome = learning_service.make_domain_rule(
            workbook, message_id=message_id, thread_id=thread_id,
            sender_email=sender_email, subject=subject,
            classification=classification, reason=reason,
        )
    elif action == "suggest-vip":
        outcome = learning_service.suggest_vip(
            workbook, message_id=message_id, thread_id=thread_id,
            sender_email=sender_email, sender_name=sender_name, subject=subject,
            classification=classification, reason=reason,
        )
    elif action == "restore":
        return gmail_dashboard_ops.restore_to_inbox(
            workbook, message_id=message_id, thread_id=thread_id,
            subject=subject, reason=reason,
        )
    else:  # trash — only reached after the dashboard's confirm page (§5)
        return gmail_dashboard_ops.trash_message(
            workbook, message_id=message_id, thread_id=thread_id,
            subject=subject, reason=reason,
        )

    label = ACTION_LABELS[action]
    event = audit_service.event_from_action(
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        classification=classification,
        reason=reason,
        action_taken=label if outcome.ok else f"{label} — refused: {outcome.message}",
    )
    audit_service.record_event(workbook, event)
    return outcome
