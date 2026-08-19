"""The audit event shape — mirrors the ``Audit_Log`` tab column for column
(CLAUDE.md §12, §13).

An :class:`AuditEvent` never carries hidden AI chain-of-thought — only the
short, user-facing rationale the classifier or a dashboard action already
produced. That's a structural guarantee, not a convention: there is no field
here wide enough to hold a reasoning transcript.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

#: Actors CLAUDE.md §12 recognizes for the Audit_Log ``actor`` column.
ACTOR_AGENT = "agent"
ACTOR_USER = "user"
ACTOR_SYSTEM = "system"

#: Subjects are truncated before they ever reach a log row (CLAUDE.md §16 —
#: never store more of an email than the audit trail actually needs).
SUBJECT_SAFE_REF_LIMIT = 120


def new_event_id() -> str:
    return uuid.uuid4().hex[:12]


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_subject_ref(subject: str) -> str:
    """A short, log-safe subject line — never the body (CLAUDE.md §16)."""
    text = (subject or "(no subject)").strip()
    return text[:SUBJECT_SAFE_REF_LIMIT]


@dataclass(frozen=True)
class AuditEvent:
    """One row of the ``Audit_Log`` tab. Nothing here has been applied to Gmail
    unless ``action_taken`` says so explicitly — Phase 9 never writes to Gmail,
    so every event produced so far is either a *proposal* (dry run) or a
    workbook-only action (a dashboard click).
    """

    gmail_message_id: str
    thread_id: str = ""
    subject_safe_ref: str = ""
    classification: str = ""
    priority: str = ""
    confidence: str = ""
    rules_triggered: str = ""
    ai_reason_summary: str = ""
    labels_before: str = ""
    labels_after: str = ""
    inbox_before: str = ""
    inbox_after: str = ""
    action_taken: str = ""
    actor: str = ACTOR_AGENT
    reversible: bool = False
    undo_status: str = "not_applicable"
    run_id: str = ""
    event_id: str = field(default_factory=new_event_id)
    timestamp: str = field(default_factory=_now_iso)

    def as_row(self) -> dict[str, str]:
        """The dict shape :meth:`SheetTable.append` / :class:`AuditRepository.record` want."""
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "gmail_message_id": self.gmail_message_id,
            "thread_id": self.thread_id,
            "subject_safe_ref": self.subject_safe_ref,
            "classification": self.classification,
            "priority": self.priority,
            "confidence": self.confidence,
            "rules_triggered": self.rules_triggered,
            "ai_reason_summary": self.ai_reason_summary,
            "labels_before": self.labels_before,
            "labels_after": self.labels_after,
            "inbox_before": self.inbox_before,
            "inbox_after": self.inbox_after,
            "action_taken": self.action_taken,
            "actor": self.actor,
            "reversible": "true" if self.reversible else "false",
            "undo_status": self.undo_status,
        }


__all__ = (
    "ACTOR_AGENT",
    "ACTOR_SYSTEM",
    "ACTOR_USER",
    "AuditEvent",
    "new_event_id",
    "new_run_id",
    "safe_subject_ref",
)
