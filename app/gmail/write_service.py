"""Manually-triggered real Gmail writes over a small batch of recent mail
(Phase 11).

This is the bounded, testable counterpart to Phase 10's acceptance run: it
runs the exact same read-only classification pipeline as ``/classify/preview``
over up to ``limit`` messages, then — only if ``confirm=True`` *and*
:func:`app.gmail.apply.check_write_gate` allows it — actually applies each
message's decision to Gmail and logs a real ``Audit_Log`` row for every one
that changed.

``confirm=False`` (the default) always behaves as a preview, regardless of
settings, the same "see it before you do it" shape as every other write-
adjacent endpoint in this app. Continuous, unattended processing of new mail
is Phase 13's job, not this module's — this is a tool for trying Phase 11 on
a few real messages under the user's direct control, not a scheduler.

A confirmed run that changes anything also writes one ``System_Runs`` row
with ``undo_available=True`` (Phase 12: :mod:`app.undo.service` is what
reads it back to find "the last run" to reverse).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.gmail import apply as gmail_apply
from app.logging_config import get_logger

log = get_logger("app.gmail.write_service")


@dataclass
class AppliedMessageResult:
    """One message's outcome — whether writes actually ran or not."""

    message_id: str
    thread_id: str
    sender_email: str
    subject: str
    labels: list[str]
    would_change: bool
    changed: bool
    action_taken: str
    labels_before: list[str]
    labels_after: list[str]


@dataclass
class ApplyReport:
    total: int
    confirm: bool
    gate_allowed: bool
    gate_reasons: list[str]
    wrote_to_gmail: bool
    changed_count: int
    results: list[AppliedMessageResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "confirm": self.confirm,
            "gate_allowed": self.gate_allowed,
            "gate_reasons": self.gate_reasons,
            "wrote_to_gmail": self.wrote_to_gmail,
            "changed_count": self.changed_count,
            "results": [
                {
                    "id": r.message_id,
                    "thread_id": r.thread_id,
                    "from": r.sender_email,
                    "subject": r.subject,
                    "labels": r.labels,
                    "would_change": r.would_change,
                    "changed": r.changed,
                    "action_taken": r.action_taken,
                    "labels_before": r.labels_before,
                    "labels_after": r.labels_after,
                }
                for r in self.results
            ],
        }


def apply_recent(
    limit: int = 10,
    query: str | None = None,
    confirm: bool = False,
    use_ai: bool = False,
    include_contacts: bool = True,
    include_workbook: bool = True,
    read_attachments: bool = True,
) -> ApplyReport:
    """Classify up to ``limit`` recent messages and, if confirmed and
    allowed, apply the result to Gmail for real. Raises ``NotConnectedError``
    (via the pipeline / workbook connect calls) the same way every other
    Gmail-backed endpoint does.
    """
    from datetime import datetime, timezone

    from app.audit import service as audit_service
    from app.classification import pipeline
    from app.gmail.write_client import IMPORTANT_LABEL, INBOX_LABEL, get_write_client
    from app.sheets.repository import ControlWorkbook

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    workbook = ControlWorkbook.connect()
    gate = gmail_apply.check_write_gate(workbook)
    will_write = confirm and gate.allowed

    results = pipeline.preview_recent(
        limit=limit,
        query=query,
        use_ai=use_ai,
        include_contacts=include_contacts,
        include_workbook=include_workbook,
        read_attachments=read_attachments,
    )

    if will_write:
        client = get_write_client()
        label_map = gmail_apply.label_name_map_for(
            client, [r.classification for r in results]
        )
    else:
        # Preview only — no Gmail call needed to know *what* would change,
        # so an identity map (name -> name) stands in for real label ids.
        client = None
        names = {INBOX_LABEL, IMPORTANT_LABEL}
        for r in results:
            names.update(r.classification.gmail_label_names)
        label_map = {name: name for name in names}

    run_id = audit_service.new_run_id() if will_write else ""
    audit_rows: list[dict[str, str]] = []
    message_results: list[AppliedMessageResult] = []
    changed_count = 0

    for r in results:
        message, decision = r.message, r.classification
        if will_write:
            change = gmail_apply.apply_to_message(client, message, decision, label_map)
            would_change = change.changed
        else:
            plan = gmail_apply.plan_change(message, decision, label_map)
            would_change = not plan.is_empty
            change = gmail_apply.AppliedChange(
                message_id=message.message_id,
                thread_id=message.thread_id,
                changed=False,
                labels_before=tuple(sorted(message.label_ids)),
                labels_after=tuple(sorted(message.label_ids)),
                inbox_before="INBOX" in message.label_ids,
                inbox_after="INBOX" in message.label_ids,
                action_taken=gmail_apply.describe_plan(plan),
            )

        if change.changed:
            changed_count += 1
            audit_rows.append(
                audit_service.event_from_applied_change(
                    change,
                    subject=message.subject,
                    classification=", ".join(decision.gmail_label_names) or "(none)",
                    priority=decision.priority.value,
                    confidence=decision.confidence,
                    reason=decision.rationale,
                    run_id=run_id,
                ).as_row()
            )

        message_results.append(
            AppliedMessageResult(
                message_id=message.message_id,
                thread_id=message.thread_id,
                sender_email=message.sender_email,
                subject=message.subject or "(no subject)",
                labels=decision.gmail_label_names,
                would_change=would_change,
                changed=change.changed,
                action_taken=change.action_taken,
                labels_before=list(change.labels_before),
                labels_after=list(change.labels_after),
            )
        )

    if audit_rows:
        workbook.audit_log.record_many(audit_rows)

    if will_write:
        workbook.system_runs.record(
            run_id=run_id,
            mode="live",
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            emails_processed=len(results),
            emails_changed=changed_count,
            errors=0,
            undo_available=changed_count > 0,
        )

    log.info(
        "gmail_apply_run_completed",
        extra={
            "total": len(results),
            "confirm": confirm,
            "gate_allowed": gate.allowed,
            "wrote_to_gmail": will_write,
            "changed_count": changed_count,
        },
    )

    return ApplyReport(
        total=len(results),
        confirm=confirm,
        gate_allowed=gate.allowed,
        gate_reasons=list(gate.reasons),
        wrote_to_gmail=will_write,
        changed_count=changed_count,
        results=message_results,
    )


__all__ = ("AppliedMessageResult", "ApplyReport", "apply_recent")
