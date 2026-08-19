"""Phase 7 — stateful follow-up (CLAUDE.md §10).

Business-day timers (US + Kenya holidays), Due Soon / Overdue deadlines,
Waiting for Reply, and Overdue Action. Everything is recomputed from current
state on each run, so a reply clears a follow-up with no stored flag to reset.
Read-only: it surfaces what needs chasing; it never touches Gmail.
"""

from __future__ import annotations

from app.followup.businessdays import (
    FOLLOWUP_BUSINESS_DAYS,
    add_business_days,
    business_days_between,
    is_business_day,
    is_holiday,
)
from app.followup.models import FollowUpItem, FollowUpKind, FollowUpReport
from app.followup.service import evaluate, evaluate_from_results, refine_report

__all__ = (
    "FOLLOWUP_BUSINESS_DAYS",
    "FollowUpItem",
    "FollowUpKind",
    "FollowUpReport",
    "add_business_days",
    "business_days_between",
    "evaluate",
    "evaluate_from_results",
    "is_business_day",
    "is_holiday",
    "refine_report",
)
