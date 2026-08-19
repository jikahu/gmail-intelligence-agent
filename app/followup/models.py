"""The records the follow-up layer produces (CLAUDE.md §10, §13).

A follow-up item is a *surfacing*, not an action. It says "this deadline is due
soon", "you're still waiting on a reply here", "this has been sitting for three
business days" — and the dashboard (Phase 8) and digest (Phase 14) show them.
Nothing here touches Gmail.

These items are recomputed from scratch on every scan. That's deliberate: there
is no stored "waiting" flag to clear later. When the other party finally
replies, the next scan simply doesn't emit the item — the state resolves itself
(CLAUDE.md §13 thread re-evaluation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FollowUpKind(str, Enum):
    DUE_SOON = "due_soon"
    OVERDUE_DEADLINE = "overdue_deadline"
    WAITING_FOR_REPLY = "waiting_for_reply"
    OVERDUE_ACTION = "overdue_action"


@dataclass(frozen=True)
class FollowUpItem:
    """One thing worth chasing, with the clock that produced it."""

    kind: FollowUpKind
    message_id: str
    thread_id: str
    subject: str
    reason: str
    #: ISO date the clock started (deadline date, or when a message was sent/received).
    since: str | None = None
    #: Business days elapsed since ``since`` (for the waiting/overdue kinds).
    business_days_elapsed: int | None = None
    #: The deadline's own date, for the deadline kinds.
    due_date: str | None = None
    #: A label this item *proposes* (e.g. AI/Waiting-For-Reply). Never applied here.
    proposed_label: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "reason": self.reason,
            "since": self.since,
            "business_days_elapsed": self.business_days_elapsed,
            "due_date": self.due_date,
            "proposed_label": self.proposed_label,
        }


@dataclass
class FollowUpReport:
    """Everything the follow-up pass turned up over one run."""

    due_soon: list[FollowUpItem] = field(default_factory=list)
    overdue_deadlines: list[FollowUpItem] = field(default_factory=list)
    waiting_for_reply: list[FollowUpItem] = field(default_factory=list)
    overdue_actions: list[FollowUpItem] = field(default_factory=list)

    def all_items(self) -> list[FollowUpItem]:
        return [
            *self.overdue_actions,
            *self.overdue_deadlines,
            *self.waiting_for_reply,
            *self.due_soon,
        ]

    def summary(self) -> dict[str, int]:
        return {
            "due_soon": len(self.due_soon),
            "overdue_deadlines": len(self.overdue_deadlines),
            "waiting_for_reply": len(self.waiting_for_reply),
            "overdue_actions": len(self.overdue_actions),
            "total": len(self.all_items()),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary(),
            "overdue_actions": [i.as_dict() for i in self.overdue_actions],
            "overdue_deadlines": [i.as_dict() for i in self.overdue_deadlines],
            "waiting_for_reply": [i.as_dict() for i in self.waiting_for_reply],
            "due_soon": [i.as_dict() for i in self.due_soon],
        }


__all__ = ("FollowUpItem", "FollowUpKind", "FollowUpReport")
