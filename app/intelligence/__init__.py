"""Phase 6 — intelligence features (CLAUDE.md §10).

Reads structured facts out of already-classified email: deadlines, money,
subscriptions, material changes, and — across a run — trips, orders and
duplicates. It never changes a classification, never touches Gmail, and never
deletes anything. The one thing it *does* write is the workbook's Deadlines,
Subscriptions and Trips tabs, and only through the explicit persistence call.
"""

from __future__ import annotations

from app.intelligence.models import (
    BatchIntelligence,
    Deadline,
    DuplicateGroup,
    FinancialDetail,
    IntelligenceReport,
    MaterialChange,
    MessageIntelligence,
    OrderContext,
    Subscription,
    TripContext,
)
from app.intelligence.service import analyze, analyze_batch, analyze_message

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
    "analyze",
    "analyze_batch",
    "analyze_message",
)
