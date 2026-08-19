"""Writing extracted intelligence into the control workbook (CLAUDE.md §12).

Phase 6 is the first phase that *writes* to the workbook. It fills three tabs
that have been empty until now — Deadlines, Subscriptions, Trips — and it does
so **only through the repository layer**, never by poking at cells.

Two properties matter here:

* **This is not a Gmail write.** Recording a deadline in a spreadsheet changes
  nothing in the user's mailbox. The dry-run guarantee is about Gmail; it is
  untouched by anything in this module.
* **Idempotent.** Rows are keyed (a message + date, a service, a trip id), so
  running a scan twice updates rows rather than duplicating them (CLAUDE.md
  §13).
"""

from __future__ import annotations

from datetime import date

from app.intelligence.models import (
    Deadline,
    IntelligenceReport,
    Subscription,
    TripContext,
)
from app.logging_config import get_logger

log = get_logger("app.intelligence.persistence")


def _deadline_row(deadline: Deadline) -> dict[str, str]:
    return {
        "message_id": deadline.message_id,
        "thread_id": deadline.thread_id,
        "deadline": deadline.label,
        "original_text": deadline.original_text,
        "normalized_date": deadline.iso,
        "status": deadline.status,
        "confidence": f"{deadline.confidence:.2f}",
        "category": deadline.category,
    }


def _subscription_row(sub: Subscription, last_seen: str) -> dict[str, str]:
    return {
        "service": sub.service,
        "sender_domain": sub.sender_domain,
        "amount": "" if sub.amount is None else f"{sub.amount:.2f}",
        "currency": sub.currency or "",
        "billing_frequency": sub.billing_frequency,
        "renewal_date": sub.renewal_date or "",
        "last_seen": last_seen,
        "review_status": sub.review_status,
    }


def _trip_row(trip: TripContext) -> dict[str, str]:
    return {
        "trip_id": trip.trip_id,
        "destination": trip.destination,
        "start_date": trip.start_date or "",
        "end_date": trip.end_date or "",
        "related_threads": ",".join(trip.related_threads),
        "status": trip.status,
    }


def persist(
    workbook: object,
    report: IntelligenceReport,
    today: date | None = None,
) -> dict[str, object]:
    """Write a report's deadlines, subscriptions and trips into the workbook.

    ``workbook`` is a :class:`~app.sheets.repository.ControlWorkbook` (typed
    loosely to avoid importing the Sheets layer into pure code). Returns a
    per-tab summary of how many rows were inserted vs. updated. A failure on one
    row is logged and skipped, never allowed to abort the whole write.
    """
    today = today or date.today()
    last_seen = today.isoformat()

    counts = {
        "deadlines": {"inserted": 0, "updated": 0, "errors": 0},
        "subscriptions": {"inserted": 0, "updated": 0, "errors": 0},
        "trips": {"inserted": 0, "updated": 0, "errors": 0},
    }

    def _write(repo, values, bucket: str) -> None:
        try:
            outcome = repo.upsert(values)
            counts[bucket][outcome] = counts[bucket].get(outcome, 0) + 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the run
            counts[bucket]["errors"] += 1
            log.warning(
                "intelligence_persist_failed",
                extra={"tab": bucket, "error": str(exc)},
            )

    deadlines_repo = workbook.deadlines
    for deadline in report.all_deadlines():
        _write(deadlines_repo, _deadline_row(deadline), "deadlines")

    subs_repo = workbook.subscriptions
    for sub in report.all_subscriptions():
        _write(subs_repo, _subscription_row(sub, last_seen), "subscriptions")

    trips_repo = workbook.trips
    for trip in report.batch.trips:
        _write(trips_repo, _trip_row(trip), "trips")

    log.info("intelligence_persisted", extra={"counts": counts})
    return counts


__all__ = ("persist",)
