"""Live progress for a Phase 15 twelve-month historical cleanup run
(CLAUDE.md §13, §14).

Only one historical run is ever active at a time (see
``app/historical/runner.py``), so a single mutable status object — updated
in place as the sweep pages through Gmail — is enough to answer "what is it
doing right now," the same shape ``RealTimeStatus``/``DigestStatus`` already
use for their own background loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HistoricalRunStatus:
    """One run's state, counters, and outcome so far."""

    #: "idle" | "running" | "completed" | "cancelled" | "failed" | "not_connected"
    state: str = "idle"
    run_id: str = ""
    months: int = 12
    #: Whether this run was asked to actually write to Gmail (not just preview).
    confirm: bool = False
    gate_allowed: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    #: Gmail's own rough count for the search window (``resultSizeEstimate``
    #: from the first page) — a ballpark for a progress display, not exact.
    estimated_total: int | None = None
    pages_processed: int = 0
    messages_seen: int = 0
    messages_processed: int = 0
    messages_changed: int = 0
    would_review_count: int = 0
    protected_count: int = 0
    errors: int = 0
    last_error: str | None = None
    cancel_requested: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "run_id": self.run_id,
            "months": self.months,
            "confirm": self.confirm,
            "gate_allowed": self.gate_allowed,
            "gate_reasons": self.gate_reasons,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "estimated_total": self.estimated_total,
            "pages_processed": self.pages_processed,
            "messages_seen": self.messages_seen,
            "messages_processed": self.messages_processed,
            "messages_changed": self.messages_changed,
            "would_review_count": self.would_review_count,
            "protected_count": self.protected_count,
            "errors": self.errors,
            "last_error": self.last_error,
            "cancel_requested": self.cancel_requested,
        }


__all__ = ("HistoricalRunStatus",)
