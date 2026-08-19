"""Grouping orders and their delivery updates (CLAUDE.md §10).

An order confirmation, a "your item shipped", an "out for delivery" and a
"delivered" are four emails about one thing. This module ties them together by
order number (or, failing that, by conversation thread), and tracks where the
order has got to. A delivery *problem* — delayed, failed, action needed — is
surfaced so the engine's Action-Required handling has something to point at.

The individual Gmail messages are never merged or changed; this is a view over
them.
"""

from __future__ import annotations

import re

from app.classification.message import EmailMessage
from app.intelligence.models import OrderContext
from app.intelligence.senders import brand_name

# order #12345 / order number ABC-123 / order no: 998877
_ORDER_ID_RE = re.compile(
    r"order\s*(?:#|number|no\.?|id|confirmation)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9-]{3,})",
    re.IGNORECASE,
)

#: Delivery lifecycle, least to most advanced. ``problem`` is tracked
#: separately as a flag, not a stage.
_STAGE_RANK = {
    "ordered": 1,
    "shipped": 2,
    "out_for_delivery": 3,
    "delivered": 4,
}

_STAGE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("delivered", r"delivered|was\s+delivered|delivery\s+complete|"
                  r"has\s+been\s+delivered"),
    ("out_for_delivery", r"out\s+for\s+delivery|arriving\s+today"),
    ("shipped", r"shipped|has\s+shipped|on\s+its\s+way|dispatched|"
                r"tracking\s+number|track\s+your"),
    ("ordered", r"order\s+confirmation|order\s+confirmed|"
                r"thank\s+you\s+for\s+your\s+order|we\s+received\s+your\s+order|"
                r"order\s+placed"),
)
_STAGE_RE = tuple((label, re.compile(p, re.IGNORECASE)) for label, p in _STAGE_PATTERNS)

_PROBLEM_RE = re.compile(
    r"delayed|delivery\s+attempt|unable\s+to\s+deliver|action\s+needed|"
    r"action\s+required|returned\s+to\s+sender|failed\s+delivery|"
    r"could\s+not\s+be\s+delivered|held\s+at|address\s+problem|"
    r"payment\s+(?:failed|declined)",
    re.IGNORECASE,
)


def _order_id(message: EmailMessage) -> str | None:
    m = _ORDER_ID_RE.search(f"{message.subject} {message.snippet}")
    if not m:
        m = _ORDER_ID_RE.search(message.body_text)
    if not m:
        return None
    token = m.group(1).strip().lstrip("#")
    # Must look like a real order number: long enough, and containing a digit.
    # The digit requirement stops words like "confirmation" or "shipped" (which
    # can follow "order") from being read as an id and merging unrelated orders.
    if len(token) < 4 or not any(ch.isdigit() for ch in token):
        return None
    return token


def _stage(message: EmailMessage) -> str | None:
    text = message.searchable_text
    for label, regex in _STAGE_RE:
        if regex.search(text):
            return label
    return None


def _has_problem(message: EmailMessage) -> bool:
    return bool(_PROBLEM_RE.search(message.searchable_text))


def group_orders(messages: list[EmailMessage]) -> list[OrderContext]:
    """Return one :class:`OrderContext` per distinct order in ``messages``.

    Messages are keyed by order number when one is present, otherwise by thread
    (so a single-thread exchange about one purchase still groups). Only messages
    that actually look like an order/shipment take part.
    """
    # key -> list of (message, stage, problem)
    buckets: dict[str, list[tuple[EmailMessage, str | None, bool]]] = {}
    order_key_for_thread: dict[str, str] = {}

    for message in messages:
        stage = _stage(message)
        oid = _order_id(message)
        if stage is None and oid is None:
            continue  # not an order/shipment email

        if oid is not None:
            key = f"order:{oid.lower()}"
            if message.thread_id:
                order_key_for_thread.setdefault(message.thread_id, key)
        elif message.thread_id and message.thread_id in order_key_for_thread:
            key = order_key_for_thread[message.thread_id]
        elif message.thread_id:
            key = f"thread:{message.thread_id}"
        else:
            key = f"msg:{message.message_id}"

        buckets.setdefault(key, []).append((message, stage, _has_problem(message)))

    orders: list[OrderContext] = []
    for key, entries in buckets.items():
        members = [entry[0] for entry in entries]
        stages = [entry[1] for entry in entries if entry[1] is not None]
        problem = any(entry[2] for entry in entries)

        if problem:
            status = "problem"
        elif stages:
            status = max(stages, key=lambda s: _STAGE_RANK.get(s, 0))
        else:
            status = "ordered"

        order_id = key.split(":", 1)[1] if key.startswith("order:") else ""
        orders.append(
            OrderContext(
                order_id=order_id,
                merchant=brand_name(members[0], fallback="Unknown merchant"),
                status=status,
                has_problem=problem,
                related_threads=tuple(
                    dict.fromkeys(m.thread_id for m in members if m.thread_id)
                ),
                related_messages=tuple(m.message_id for m in members),
            )
        )
    return orders


__all__ = ("group_orders",)
