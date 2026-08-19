"""Undo Last Run (Phase 12, CLAUDE.md §13, §14).

Reverses the most recent real Gmail-write run — a confirmed batch
``/gmail/apply``, or a single dashboard Restore/Trash click (Phase 11 gives
every real write its own ``run_id`` and ``System_Runs`` row specifically so
this phase has something to find; a single click is a "run" of one message).

**Restoration, not replay.** Every affected message goes back to the exact
``labels_before``/``inbox_before`` state Phase 11 recorded in ``Audit_Log`` —
this does not re-run the classifier. The classifier's opinion may have
changed since the original write; undo's job is only to reverse what *this
app* did, not to reclassify the mail.

**The same write gate as everything else.** :func:`app.gmail.apply.check_write_gate`
is checked before any undo write happens — ``DRY_RUN=true`` blocks Undo the
same as it blocks every other write path, with no carve-out for "the
original write already happened for real." One rule with no exceptions is
easier to trust than one with a special case (CLAUDE.md §21).

**Never pretend an operation is reversible if Gmail no longer allows it**
(§5). A Trashed message past Gmail's 30-day window, or a message removed
some other way, can't be recovered — a 404 from Gmail is reported honestly
as "no longer recoverable," per message, rather than crashing the whole undo
or silently claiming success.

**An undo of an undo isn't a thing.** Once a run has been processed here,
``System_Runs.mark_undone`` takes it out of contention — re-running Undo
finds the *next* most recent undoable run, not the same one again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.audit.models import ACTOR_USER, AuditEvent
from app.gmail import apply as gmail_apply
from app.gmail.write_client import TRASH_LABEL
from app.logging_config import get_logger

log = get_logger("app.undo.service")


def _split_labels(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _is_reversible(row) -> bool:
    return (row.get("reversible") or "").strip().lower() == "true"


@dataclass(frozen=True)
class UndoPreviewMessage:
    message_id: str
    subject: str
    action_taken: str
    labels_before: list[str]
    labels_after: list[str]


@dataclass(frozen=True)
class UndoPreview:
    run_id: str
    mode: str
    completed_at: str
    message_count: int
    messages: list[UndoPreviewMessage]


@dataclass(frozen=True)
class UndoMessageOutcome:
    message_id: str
    outcome: str  # "restored" | "already_ok" | "not_found"
    detail: str


@dataclass(frozen=True)
class UndoResult:
    run_id: str
    #: "done" | "gate_closed" | "not_found"
    status: str
    message: str
    gate_reasons: tuple[str, ...] = ()
    outcomes: list[UndoMessageOutcome] = field(default_factory=list)

    @property
    def restored_count(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "restored")


def _preview_for(workbook, run_row) -> UndoPreview:
    run_id = run_row.get("run_id")
    audit_rows = [r for r in workbook.audit_log.for_run(run_id) if _is_reversible(r)]
    messages = [
        UndoPreviewMessage(
            message_id=r.get("gmail_message_id"),
            subject=r.get("subject_safe_ref"),
            action_taken=r.get("action_taken"),
            labels_before=_split_labels(r.get("labels_before")),
            labels_after=_split_labels(r.get("labels_after")),
        )
        for r in audit_rows
    ]
    return UndoPreview(
        run_id=run_id,
        mode=run_row.get("mode"),
        completed_at=run_row.get("completed_at"),
        message_count=len(messages),
        messages=messages,
    )


def preview_last_run(workbook) -> UndoPreview | None:
    """The most recent undoable run, or ``None`` if there's nothing to undo."""
    run_row = workbook.system_runs.latest_undoable()
    if run_row is None:
        return None
    return _preview_for(workbook, run_row)


def preview_run(workbook, run_id: str) -> UndoPreview | None:
    run_row = workbook.system_runs.for_run(run_id)
    if run_row is None:
        return None
    return _preview_for(workbook, run_row)


def undo_run(workbook, run_id: str) -> UndoResult:
    """Actually reverse one run. Callers are responsible for having already
    confirmed with the user (the dashboard route's confirm page, or an
    explicit ``confirm=true`` on the JSON route) — this function does not
    ask again, the same contract :func:`app.gmail.dashboard_ops.trash_message`
    already uses for Trash.
    """
    gate = gmail_apply.check_write_gate(workbook)
    if not gate.allowed:
        return UndoResult(
            run_id=run_id,
            status="gate_closed",
            message=f"Can't undo yet — {gate.reason}.",
            gate_reasons=gate.reasons,
        )

    run_row = workbook.system_runs.for_run(run_id)
    if run_row is None or (run_row.get("undo_available") or "").strip().lower() != "true":
        return UndoResult(
            run_id=run_id,
            status="not_found",
            message="That run isn't available to undo — it may already have been undone.",
        )

    from googleapiclient.errors import HttpError

    from app.gmail.write_client import get_write_client

    audit_rows = [r for r in workbook.audit_log.for_run(run_id) if _is_reversible(r)]
    write_client = get_write_client()
    outcomes: list[UndoMessageOutcome] = []
    new_audit_rows: list[dict[str, str]] = []

    for row in audit_rows:
        message_id = row.get("gmail_message_id")
        thread_id = row.get("thread_id")
        subject = row.get("subject_safe_ref")
        target_labels = set(_split_labels(row.get("labels_before")))
        after_labels = set(_split_labels(row.get("labels_after")))
        was_trashed = TRASH_LABEL in after_labels and TRASH_LABEL not in target_labels

        try:
            current = set(gmail_apply.fetch_current_labels(message_id))
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                outcomes.append(
                    UndoMessageOutcome(
                        message_id, "not_found",
                        "no longer exists in Gmail — nothing to restore",
                    )
                )
                continue
            raise

        if was_trashed:
            if TRASH_LABEL not in current:
                outcomes.append(
                    UndoMessageOutcome(message_id, "already_ok", "already out of Trash")
                )
                continue
            write_client.untrash_message(message_id)
            outcomes.append(
                UndoMessageOutcome(message_id, "restored", "restored out of Gmail Trash")
            )
            action_taken = "Undo Last Run: restored out of Gmail Trash"
        else:
            to_add = target_labels - current
            to_remove = current - target_labels
            if not to_add and not to_remove:
                outcomes.append(
                    UndoMessageOutcome(message_id, "already_ok", "already in its previous state")
                )
                continue
            write_client.modify_message(
                message_id, add_label_ids=list(to_add), remove_label_ids=list(to_remove)
            )
            outcomes.append(
                UndoMessageOutcome(
                    message_id, "restored", "labels/Inbox restored to their previous state"
                )
            )
            action_taken = "Undo Last Run: restored previous labels/Inbox state"

        new_audit_rows.append(
            AuditEvent(
                run_id=run_id,
                gmail_message_id=message_id,
                thread_id=thread_id,
                subject_safe_ref=subject,
                action_taken=action_taken,
                actor=ACTOR_USER,
                reversible=False,
                undo_status="not_applicable",
            ).as_row()
        )

    if new_audit_rows:
        workbook.audit_log.record_many(new_audit_rows)
    workbook.system_runs.mark_undone(run_id)

    log.info(
        "undo_run_completed",
        extra={"run_id": run_id, "restored": sum(1 for o in outcomes if o.outcome == "restored")},
    )

    return UndoResult(
        run_id=run_id,
        status="done",
        message=f"Undo complete for run {run_id}.",
        outcomes=outcomes,
    )


__all__ = (
    "UndoMessageOutcome",
    "UndoPreview",
    "UndoPreviewMessage",
    "UndoResult",
    "preview_last_run",
    "preview_run",
    "undo_run",
)
