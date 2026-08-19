"""Turn a classification decision, a dashboard click, or a real Gmail write
into an Audit_Log row (CLAUDE.md §12, §13).

Three event shapes, all built here:

* :func:`event_from_result` — one row per message from a *dry-run*
  classification pass. Zero Gmail writes happen on this path, so
  ``labels_before``/``labels_after`` (and ``inbox_before``/``inbox_after``)
  are always identical: nothing changed. The proposal itself lives in
  ``classification``, ``rules_triggered`` and ``ai_reason_summary``. This is
  what TECHNICAL_STATUS.md calls "still all 'before' in dry run": the audit
  trail never claims a Gmail change happened when it didn't.
* :func:`event_from_action` — one row per workbook-only dashboard action
  (Keep, Review Correct, a rule suggestion, a VIP suggestion).
* :func:`event_from_applied_change` — one row per *real* Gmail write (Phase
  11: an automated label/archive/restore apply, or a dashboard Restore/Trash
  click). ``labels_before``/``labels_after`` and ``inbox_before``/
  ``inbox_after`` are the message's genuine state, not placeholders, because
  something in Gmail actually changed.

Nothing here stores hidden AI chain-of-thought — only the short, user-facing
rationale already computed by the classifier.
"""

from __future__ import annotations

from app.audit.models import (
    ACTOR_AGENT,
    ACTOR_USER,
    AuditEvent,
    new_run_id,
    safe_subject_ref,
)
from app.logging_config import get_logger

log = get_logger("app.audit.service")

__all__ = (
    "event_from_action",
    "event_from_applied_change",
    "event_from_result",
    "new_run_id",
    "record_event",
    "record_run",
)


def _confidence_str(confidence: float | None) -> str:
    return f"{confidence:.2f}" if confidence is not None else ""


def event_from_result(result, run_id: str) -> AuditEvent:
    """Build the audit row for one message's proposed classification.

    ``result`` is a :class:`app.classification.pipeline.PreviewResult` — typed
    loosely so this module doesn't have to import the classification package
    just to read a few attributes.
    """
    message, decision = result.message, result.classification
    current_labels = ", ".join(sorted(message.label_ids)) if message.label_ids else ""
    inbox_state = "true" if message.in_inbox else "false"

    return AuditEvent(
        run_id=run_id,
        gmail_message_id=message.message_id,
        thread_id=message.thread_id,
        subject_safe_ref=safe_subject_ref(message.subject),
        classification=", ".join(decision.gmail_label_names) or "(none)",
        priority=decision.priority.value,
        confidence=_confidence_str(decision.confidence),
        rules_triggered="; ".join(decision.rules_triggered),
        ai_reason_summary=decision.rationale,
        labels_before=current_labels,
        labels_after=current_labels,
        inbox_before=inbox_state,
        inbox_after=inbox_state,
        action_taken="proposed only — dry run, no Gmail change made",
        actor=ACTOR_AGENT,
        reversible=False,
        undo_status="not_applicable (dry run)",
    )


def event_from_action(
    *,
    message_id: str,
    thread_id: str,
    subject: str,
    classification: str,
    priority: str = "",
    confidence: float | None = None,
    reason: str = "",
    action_taken: str,
    run_id: str = "",
) -> AuditEvent:
    """Build the audit row for a dashboard action (Keep, Review Correct, ...).

    Dashboard actions in Phase 9 only ever write to the workbook (Review
    Feedback, a rule suggestion, a VIP suggestion) — never to Gmail — so
    ``labels_before``/``labels_after`` are identical and there is nothing to
    undo yet.
    """
    return AuditEvent(
        run_id=run_id,
        gmail_message_id=message_id,
        thread_id=thread_id,
        subject_safe_ref=safe_subject_ref(subject),
        classification=classification,
        priority=priority,
        confidence=_confidence_str(confidence),
        rules_triggered="",
        ai_reason_summary=reason,
        labels_before=classification,
        labels_after=classification,
        inbox_before="",
        inbox_after="",
        action_taken=action_taken,
        actor=ACTOR_USER,
        reversible=False,
        undo_status="not_applicable (workbook-only action)",
    )


def event_from_applied_change(
    change,
    *,
    subject: str,
    classification: str,
    priority: str = "",
    confidence: float | None = None,
    reason: str = "",
    run_id: str = "",
    actor: str = ACTOR_AGENT,
) -> AuditEvent:
    """Build the audit row for one *real* Gmail write.

    ``change`` is an :class:`app.gmail.apply.AppliedChange` — typed loosely
    here for the same reason :func:`event_from_result` doesn't import the
    classification package just to read a few attributes.

    Gmail label/archive/restore changes and Trash are all reversible while
    Gmail still has the data (a label can be re-added, Trash can be
    untrashed within Gmail's 30-day window), so ``reversible=True`` and
    ``undo_status="not_undone"`` — Phase 12's Undo Last Run is what will act
    on that status; nothing today reads it back.
    """
    return AuditEvent(
        run_id=run_id,
        gmail_message_id=change.message_id,
        thread_id=change.thread_id,
        subject_safe_ref=safe_subject_ref(subject),
        classification=classification,
        priority=priority,
        confidence=_confidence_str(confidence),
        rules_triggered="",
        ai_reason_summary=reason,
        labels_before=", ".join(change.labels_before),
        labels_after=", ".join(change.labels_after),
        inbox_before="true" if change.inbox_before else "false",
        inbox_after="true" if change.inbox_after else "false",
        action_taken=change.action_taken,
        actor=actor,
        reversible=True,
        undo_status="not_undone",
    )


def record_event(workbook, event: AuditEvent) -> None:
    """Append one audit row through the repository layer."""
    workbook.audit_log.record(event.as_row())


def record_run(workbook, results, run_id: str | None = None) -> str:
    """Write one Audit_Log row per classified message. Returns the run id.

    ``results`` is a list of :class:`PreviewResult`. Every message in a batch
    shares one ``run_id`` so a later phase (Undo Last Run, Phase 12) can pull
    the whole run back out with :meth:`AuditRepository.for_run`.

    Written as a single batch call, not one append per message — Sheets API's
    write quota is 60 requests/minute/user, and a real acceptance run (250
    messages) blows through that in under a minute if each row is its own
    request.
    """
    run_id = run_id or new_run_id()
    rows = [event_from_result(result, run_id).as_row() for result in results]
    workbook.audit_log.record_many(rows)
    log.info("audit_run_recorded", extra={"run_id": run_id, "count": len(results)})
    return run_id
