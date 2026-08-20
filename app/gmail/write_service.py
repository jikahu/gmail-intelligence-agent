"""Manually-triggered real Gmail writes over a small batch of recent mail.

This runs the exact same read-only classification pipeline as
``/classify/preview`` over up to ``limit`` messages, then — only if
``confirm=True`` *and* :func:`app.gmail.apply.check_write_gate` allows it —
actually applies each message's decision to Gmail.

``confirm=False`` (the default) always behaves as a preview, regardless of
settings, the same "see it before you do it" shape as every other write-
adjacent endpoint in this app. Continuous, unattended processing of new mail
is the real-time poller's job (:mod:`app.scheduling.poller`), not this
module's — this is a tool for trying a write on a few real messages under the
user's direct control, not a scheduler.
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
    include_rules: bool = True,
) -> ApplyReport:
    """Classify up to ``limit`` recent messages and, if confirmed and
    allowed, apply the result to Gmail for real. Raises ``NotConnectedError``
    the same way every other Gmail-backed endpoint does.
    """
    from app.classification import pipeline
    from app.gmail.write_client import IMPORTANT_LABEL, INBOX_LABEL, get_write_client

    gate = gmail_apply.check_write_gate()
    will_write = confirm and gate.allowed

    results = pipeline.preview_recent(
        limit=limit,
        query=query,
        use_ai=use_ai,
        include_contacts=include_contacts,
        include_rules=include_rules,
    )

    vendor_labels: dict[str, str | None] = {}
    if will_write:
        client = get_write_client()
        for r in results:
            vendor_labels[r.message.message_id] = gmail_apply.vendor_label_for(
                client, r.message
            )
        label_map = gmail_apply.label_name_map_for(
            client,
            [r.classification for r in results],
            vendor_label_names={v for v in vendor_labels.values() if v},
        )
    else:
        # Preview only — no Gmail call needed to know *what* would change,
        # so an identity map (name -> name) stands in for real label ids.
        client = None
        names = {INBOX_LABEL, IMPORTANT_LABEL}
        for r in results:
            names.update(r.classification.gmail_label_names)
        label_map = {name: name for name in names}

    message_results: list[AppliedMessageResult] = []
    changed_count = 0

    for r in results:
        message, decision = r.message, r.classification
        vendor_label_name = vendor_labels.get(message.message_id)
        if will_write:
            change = gmail_apply.apply_to_message(
                client, message, decision, label_map, vendor_label_name
            )
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
