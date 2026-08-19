"""The records Phase 6 produces — plain data, no behaviour.

Everything the intelligence layer extracts ends up as one of these frozen
dataclasses. They are deliberately dumb: no I/O, no Gmail, no Sheets. The
extractors build them, the persistence layer writes them, the dashboard reads
them. Keeping them free of dependencies is what lets every extractor be tested
with a hand-written fixture and no network.

Dates that are always present are kept as :class:`datetime.date`. Dates that
are optional (a renewal, an effective date) are stored as ISO strings or
``None``, because "we didn't find one" is a real and common answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Deadline:
    """A date the reader is expected to act on or before (CLAUDE.md §10)."""

    message_id: str
    thread_id: str
    normalized_date: date
    original_text: str
    confidence: float
    #: payment / response / renewal / appointment / registration / interview /
    #: delivery / generic.
    category: str
    action_required: bool
    #: ``upcoming`` or ``overdue`` by plain calendar date. Business-day nuance
    #: (``due_soon``) and holiday-aware timers are Phase 7, not here.
    status: str
    #: Short human label for the dashboard, e.g. "Payment due".
    label: str = ""

    @property
    def iso(self) -> str:
        return self.normalized_date.isoformat()

    def as_dict(self) -> dict[str, object]:
        return {
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "deadline": self.label,
            "original_text": self.original_text,
            "normalized_date": self.iso,
            "status": self.status,
            "confidence": round(self.confidence, 2),
            "category": self.category,
            "action_required": self.action_required,
        }


@dataclass(frozen=True)
class FinancialDetail:
    """The minimum money detail worth keeping about one email (CLAUDE.md §7).

    ``account_ref`` is never more than the last four digits — see
    :mod:`app.intelligence.money`.
    """

    kind: str  # bill / payment / refund / statement / transaction / charge / unknown
    amount: float | None = None
    currency: str | None = None
    due_date: str | None = None  # ISO
    account_ref: str | None = None  # last 4 only
    original_text: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "amount": self.amount,
            "currency": self.currency,
            "due_date": self.due_date,
            "account_ref": (f"••••{self.account_ref}" if self.account_ref else None),
            "original_text": self.original_text,
        }


@dataclass(frozen=True)
class Subscription:
    """A recurring charge or membership. The agent may *suggest* review of it;
    it never cancels anything (CLAUDE.md §10, §20)."""

    service: str
    sender_domain: str
    amount: float | None = None
    currency: str | None = None
    #: monthly / annually / weekly / quarterly / unknown.
    billing_frequency: str = "unknown"
    renewal_date: str | None = None  # ISO
    #: "" or "suggested_review" — a suggestion, never an action.
    review_status: str = ""
    message_id: str = ""
    thread_id: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "sender_domain": self.sender_domain,
            "amount": self.amount,
            "currency": self.currency,
            "billing_frequency": self.billing_frequency,
            "renewal_date": self.renewal_date,
            "review_status": self.review_status,
        }


@dataclass(frozen=True)
class MaterialChange:
    """A change to terms, price, fees or coverage — typically P2 (CLAUDE.md §10)."""

    kind: str  # price / fee / interest_rate / terms / coverage / service
    summary: str
    old_value: str | None = None
    new_value: str | None = None
    effective_date: str | None = None  # ISO
    action_required: bool = False
    message_id: str = ""
    thread_id: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "effective_date": self.effective_date,
            "action_required": self.action_required,
        }


@dataclass(frozen=True)
class TripContext:
    """Several travel emails grouped into one trip (CLAUDE.md §10).

    The individual Gmail messages are never merged or altered — this is a view
    laid over them.
    """

    trip_id: str
    destination: str
    start_date: str | None  # ISO
    end_date: str | None  # ISO
    related_threads: tuple[str, ...] = ()
    related_messages: tuple[str, ...] = ()
    status: str = "unknown"  # upcoming / past / unknown
    segment_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "trip_id": self.trip_id,
            "destination": self.destination,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "related_threads": list(self.related_threads),
            "related_messages": list(self.related_messages),
            "status": self.status,
            "segment_count": self.segment_count,
        }


@dataclass(frozen=True)
class OrderContext:
    """Order-confirmation + shipment emails grouped by order (CLAUDE.md §10)."""

    order_id: str
    merchant: str
    status: str  # ordered / shipped / out_for_delivery / delivered / problem
    has_problem: bool = False
    related_threads: tuple[str, ...] = ()
    related_messages: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "merchant": self.merchant,
            "status": self.status,
            "has_problem": self.has_problem,
            "related_threads": list(self.related_threads),
            "related_messages": list(self.related_messages),
        }


@dataclass(frozen=True)
class DuplicateGroup:
    """Two or more near-identical messages. Raises Review confidence; never a
    reason to delete anything (CLAUDE.md §9)."""

    fingerprint: str
    message_ids: tuple[str, ...]
    representative_subject: str

    @property
    def count(self) -> int:
        return len(self.message_ids)

    def as_dict(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "message_ids": list(self.message_ids),
            "subject": self.representative_subject,
            "count": self.count,
        }


@dataclass
class MessageIntelligence:
    """Everything extracted from a single email."""

    message_id: str
    thread_id: str = ""
    deadlines: list[Deadline] = field(default_factory=list)
    financial: FinancialDetail | None = None
    subscription: Subscription | None = None
    material_change: MaterialChange | None = None
    is_expired: bool = False

    @property
    def has_content(self) -> bool:
        return bool(
            self.deadlines
            or self.financial
            or self.subscription
            or self.material_change
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "deadlines": [d.as_dict() for d in self.deadlines],
            "financial": self.financial.as_dict() if self.financial else None,
            "subscription": (
                self.subscription.as_dict() if self.subscription else None
            ),
            "material_change": (
                self.material_change.as_dict() if self.material_change else None
            ),
            "is_expired": self.is_expired,
        }


@dataclass
class BatchIntelligence:
    """Cross-message groupings — need more than one email to compute."""

    trips: list[TripContext] = field(default_factory=list)
    orders: list[OrderContext] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "trips": [t.as_dict() for t in self.trips],
            "orders": [o.as_dict() for o in self.orders],
            "duplicate_groups": [g.as_dict() for g in self.duplicate_groups],
        }


@dataclass
class IntelligenceReport:
    """Per-message intelligence plus the cross-message view over a run."""

    messages: dict[str, MessageIntelligence] = field(default_factory=dict)
    batch: BatchIntelligence = field(default_factory=BatchIntelligence)

    def for_message(self, message_id: str) -> MessageIntelligence | None:
        return self.messages.get(message_id)

    def all_deadlines(self) -> list[Deadline]:
        out: list[Deadline] = []
        for intel in self.messages.values():
            out.extend(intel.deadlines)
        return out

    def all_subscriptions(self) -> list[Subscription]:
        return [
            intel.subscription
            for intel in self.messages.values()
            if intel.subscription is not None
        ]

    def summary(self) -> dict[str, object]:
        return {
            "messages_with_intelligence": sum(
                1 for i in self.messages.values() if i.has_content
            ),
            "deadlines": len(self.all_deadlines()),
            "overdue": sum(
                1 for d in self.all_deadlines() if d.status == "overdue"
            ),
            "financial": sum(
                1 for i in self.messages.values() if i.financial is not None
            ),
            "subscriptions": len(self.all_subscriptions()),
            "material_changes": sum(
                1 for i in self.messages.values() if i.material_change is not None
            ),
            "expired": sum(1 for i in self.messages.values() if i.is_expired),
            "trips": len(self.batch.trips),
            "orders": len(self.batch.orders),
            "orders_with_problems": sum(
                1 for o in self.batch.orders if o.has_problem
            ),
            "duplicate_groups": len(self.batch.duplicate_groups),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary(),
            "by_message": {
                mid: intel.as_dict()
                for mid, intel in self.messages.items()
                if intel.has_content
            },
            "batch": self.batch.as_dict(),
        }


__all__ = (
    "BatchIntelligence",
    "Deadline",
    "DuplicateGroup",
    "FinancialDetail",
    "IntelligenceReport",
    "MaterialChange",
    "MessageIntelligence",
    "OrderContext",
    "Subscription",
    "TripContext",
)
