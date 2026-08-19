"""Restore to Inbox and Trash — the two Review-queue actions that touch real
Gmail (Phase 11, CLAUDE.md §13).

Both are direct, single-message user actions, not classification-driven —
unlike the batch :func:`app.gmail.write_service.apply_recent` orchestrator,
there's no "what would the rules engine decide" question here. Restore
always adds ``INBOX``. Trash always moves to Gmail's own Trash, which keeps
a message recoverable for 30 days — there is no permanent-delete path
anywhere in this app, structurally: nothing here calls anything but
``modify`` and ``trash``.

Both still go through the same write gate as everything else
(:func:`app.gmail.apply.check_write_gate`) — a user clicking Restore or
Trash before live writes are turned on gets a clear refusal, not a silent
no-op or a confusing error. The dashboard route layer is what enforces
Trash's two-step confirmation (CLAUDE.md §5); by the time either function
here runs, that confirmation has already happened.

Each call gets its own ``run_id`` and a matching ``System_Runs`` row
(``mode="live"``, ``undo_available=True``) — a single click is a "run" of
one message, exactly as eligible for Phase 12's Undo Last Run as a batch
apply. Without this, only batch runs would ever be undoable, which would
make Undo far less useful than the button it's supposed to reverse.
"""

from __future__ import annotations

from app.audit import service as audit_service
from app.audit.models import ACTOR_USER
from app.gmail import apply as gmail_apply
from app.gmail.write_client import INBOX_LABEL
from app.learning.models import FeedbackOutcome
from app.logging_config import get_logger

log = get_logger("app.gmail.dashboard_ops")


def _record_run(workbook, run_id: str, *, started_at: str, changed: bool) -> None:
    from datetime import datetime, timezone

    workbook.system_runs.record(
        run_id=run_id,
        mode="live",
        started_at=started_at,
        completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        emails_processed=1,
        emails_changed=1 if changed else 0,
        errors=0,
        undo_available=changed,
    )


def _missing_write_scope() -> str | None:
    """A friendly refusal instead of a raw 403 from Google, when the stored
    token predates Phase 11 and never granted ``gmail.modify``."""
    from app.gmail.tokens import missing_scopes

    missing = missing_scopes()
    if missing:
        return (
            "your Google account was connected before Gmail write access was "
            "added — reconnect at /oauth/start to grant: " + ", ".join(missing)
        )
    return None


def restore_to_inbox(
    workbook, *, message_id: str, thread_id: str, subject: str, reason: str = ""
) -> FeedbackOutcome:
    from datetime import datetime, timezone

    scope_problem = _missing_write_scope()
    if scope_problem:
        return FeedbackOutcome(ok=False, message=f"Can't restore yet — {scope_problem}.")
    gate = gmail_apply.check_write_gate(workbook)
    if not gate.allowed:
        return FeedbackOutcome(ok=False, message=f"Can't restore yet — {gate.reason}.")
    if not message_id:
        return FeedbackOutcome(ok=False, message="No message to restore.")

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    before = gmail_apply.fetch_current_labels(message_id)
    if INBOX_LABEL in before:
        return FeedbackOutcome(ok=True, message="Already in your Inbox.")

    from app.audit.models import new_run_id
    from app.gmail.write_client import get_write_client

    run_id = new_run_id()
    response = get_write_client().modify_message(message_id, add_label_ids=[INBOX_LABEL])
    after = list(response.get("labelIds") or [])

    change = gmail_apply.AppliedChange(
        message_id=message_id,
        thread_id=thread_id,
        changed=True,
        labels_before=tuple(sorted(before)),
        labels_after=tuple(sorted(after)),
        inbox_before=False,
        inbox_after=INBOX_LABEL in after,
        action_taken="restored to Inbox",
    )
    audit_service.record_event(
        workbook,
        audit_service.event_from_applied_change(
            change,
            subject=subject,
            classification="",
            reason=reason or "restored by user",
            actor=ACTOR_USER,
            run_id=run_id,
        ),
    )
    _record_run(workbook, run_id, started_at=started_at, changed=True)
    log.info("gmail_restored_to_inbox", extra={"message_id": message_id})
    return FeedbackOutcome(ok=True, message="Moved back to your Inbox.")


def trash_message(
    workbook, *, message_id: str, thread_id: str, subject: str, reason: str = ""
) -> FeedbackOutcome:
    """Move to Gmail's Trash. Only ever called after the user has confirmed
    on the dedicated confirmation page — this function itself does not ask
    again, so callers (the dashboard route) must not skip that step.
    """
    from datetime import datetime, timezone

    scope_problem = _missing_write_scope()
    if scope_problem:
        return FeedbackOutcome(ok=False, message=f"Can't move to Trash yet — {scope_problem}.")
    gate = gmail_apply.check_write_gate(workbook)
    if not gate.allowed:
        return FeedbackOutcome(ok=False, message=f"Can't move to Trash yet — {gate.reason}.")
    if not message_id:
        return FeedbackOutcome(ok=False, message="No message to move to Trash.")

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    before = gmail_apply.fetch_current_labels(message_id)

    from app.audit.models import new_run_id
    from app.gmail.write_client import get_write_client

    run_id = new_run_id()
    response = get_write_client().trash_message(message_id)
    after = list(response.get("labelIds") or [])

    change = gmail_apply.AppliedChange(
        message_id=message_id,
        thread_id=thread_id,
        changed=True,
        labels_before=tuple(sorted(before)),
        labels_after=tuple(sorted(after)),
        inbox_before=INBOX_LABEL in before,
        inbox_after=INBOX_LABEL in after,
        action_taken="moved to Gmail Trash (recoverable for 30 days — not a permanent delete)",
    )
    audit_service.record_event(
        workbook,
        audit_service.event_from_applied_change(
            change,
            subject=subject,
            classification="",
            reason=reason or "trashed by user (confirmed)",
            actor=ACTOR_USER,
            run_id=run_id,
        ),
    )
    _record_run(workbook, run_id, started_at=started_at, changed=True)
    log.info("gmail_trashed", extra={"message_id": message_id})
    return FeedbackOutcome(
        ok=True,
        message="Moved to Gmail Trash. You can recover it from Trash within 30 days.",
    )


__all__ = ("restore_to_inbox", "trash_message")
