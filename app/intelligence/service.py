"""Orchestration for the intelligence layer (CLAUDE.md §10).

Two passes:

* **Per message** — deadlines, financial detail, subscription, material change,
  and whether the message looks expired. Pure and independent per email.
* **Across the run** — trips, orders and duplicate groups, which only mean
  anything when you can see several messages at once.

Everything here is read-only and side-effect free. It reads a
:class:`~app.classification.engine.Classification` when one is offered (to know
whether the engine already decided something is actionable) but it never
changes a classification, never touches Gmail, and never writes to the
workbook. Persistence is a separate, explicit step
(:mod:`app.intelligence.persistence`).
"""

from __future__ import annotations

from datetime import date
from typing import Mapping

from app.classification import patterns
from app.classification.labels import Label
from app.classification.message import EmailMessage
from app.intelligence import (
    deadlines as deadlines_mod,
    duplicates as duplicates_mod,
    financial as financial_mod,
    material as material_mod,
    orders as orders_mod,
    subscriptions as subscriptions_mod,
    travel as travel_mod,
)
from app.intelligence.models import (
    BatchIntelligence,
    IntelligenceReport,
    MessageIntelligence,
)


def _action_required(classification: object | None) -> bool:
    return bool(getattr(classification, "action_required", False))


def _expired_hint(classification: object | None) -> bool:
    labels = getattr(classification, "labels", None)
    return bool(labels) and Label.EXPIRED in labels


def analyze_message(
    message: EmailMessage,
    today: date,
    classification: object | None = None,
) -> MessageIntelligence:
    """Extract everything single-message from one email."""
    action_required = _action_required(classification)
    is_expired = (
        patterns.EXPIRED.matches(message.subject_and_snippet)
        or _expired_hint(classification)
    )
    return MessageIntelligence(
        message_id=message.message_id,
        thread_id=message.thread_id,
        deadlines=deadlines_mod.extract_deadlines(
            message, today, action_required=action_required
        ),
        financial=financial_mod.extract_financial(message, today),
        subscription=subscriptions_mod.extract_subscription(message, today),
        material_change=material_mod.extract_material_change(message, today),
        is_expired=is_expired,
    )


def analyze_batch(messages: list[EmailMessage], today: date) -> BatchIntelligence:
    """Compute the cross-message groupings for a run."""
    return BatchIntelligence(
        trips=travel_mod.group_trips(messages, today),
        orders=orders_mod.group_orders(messages),
        duplicate_groups=duplicates_mod.find_duplicates(messages),
    )


def analyze(
    messages: list[EmailMessage],
    classifications: Mapping[str, object] | None = None,
    today: date | None = None,
) -> IntelligenceReport:
    """Run the full intelligence pass over a batch of messages.

    ``classifications`` is an optional map from ``message_id`` to that message's
    :class:`Classification`, used only as a hint (is it actionable? did the
    engine mark it expired?). ``today`` anchors overdue/upcoming and trip
    status; it defaults to the real date but is injectable for tests.
    """
    today = today or date.today()
    classifications = classifications or {}

    per_message: dict[str, MessageIntelligence] = {}
    for message in messages:
        per_message[message.message_id] = analyze_message(
            message, today, classification=classifications.get(message.message_id)
        )

    return IntelligenceReport(
        messages=per_message,
        batch=analyze_batch(messages, today),
    )


__all__ = ("analyze", "analyze_batch", "analyze_message")
