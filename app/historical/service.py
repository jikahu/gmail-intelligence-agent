"""The 12-month historical cleanup sweep (CLAUDE.md §13, §14 Phase 15).

Run separately from real-time processing (Phase 13) and the digest
(Phase 14) — this is a deliberate, manually-triggered pass over up to the
last 12 months of mail, not something either background loop ever starts on
its own. :func:`run_historical_cleanup` is the one worker function; the
background task wrapper that lets it run without blocking an HTTP request
lives in ``app/historical/runner.py``.

Three ideas carried over from earlier phases, applied at a much larger scale:

* **The same write gate, the same confirm-first shape as Phase 11's
  ``apply_recent``.** ``confirm=False`` (the default) always previews,
  regardless of settings — CLAUDE.md §13's "start with a dry run" falls out
  of that default rather than needing a separate mode.
* **A safety-invariant violation aborts the whole run, not just one
  message** (Phase 10's own philosophy: "a crash in a dry run beats a
  hidden email" — extended here to a live run too, since a protected email
  ever being routed to Review is a bug in the classifier itself, not a
  one-off data problem, and likely affects other messages in the same
  sweep). Every other per-message failure (a transient Gmail error that
  outlasted its retries, one bad message) is caught, logged, and counted —
  the sweep keeps going, the same "log failures, keep going" contract
  Phase 13's poller applies to its own cycles.
* **Cost and storage discipline (§17).** A *preview* pass never writes
  per-message ``Audit_Log`` rows — CLAUDE.md asks this phase to "produce
  metrics," not to write tens of thousands of "nothing changed" rows to a
  spreadsheet. A *confirmed* pass writes one row only for a message that
  actually changed, exactly like ``apply_recent`` already does — a message
  already in its desired state produces no API call and no log row.
"""

from __future__ import annotations

import calendar
import time
from datetime import date, datetime, timezone
from typing import Callable

from app.historical.models import HistoricalRunStatus
from app.logging_config import get_logger

log = get_logger("app.historical.service")

#: Default page size for the message-id listing. Gmail allows up to 500;
#: 100 keeps each page's worth of full-message fetches to a manageable size.
DEFAULT_BATCH_SIZE = 100

#: A short, polite pause between pages — mostly to give the cancellation
#: check a natural breathing point on a long sweep, not because per-second
#: quota is at real risk from serial, retried calls (see module docstring).
DEFAULT_PAGE_PAUSE_SECONDS = 1.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def twelve_months_ago(today: date | None = None, months: int = 12) -> date:
    """Calendar-month subtraction, not a fixed 365-day offset — so "12
    months ago" from any date lands on the same day one calendar year back
    (with Feb 29 clamped to Feb 28 in a non-leap target year), not a
    slightly-off day count. Stdlib only (CLAUDE.md §3's holiday-math rule
    applied here too: never hard-code date arithmetic that a library would
    get subtly wrong across month/year boundaries).
    """
    today = today or date.today()
    total_months = today.year * 12 + (today.month - 1) - months
    year, month0 = divmod(total_months, 12)
    month = month0 + 1
    last_day_of_target_month = calendar.monthrange(year, month)[1]
    return date(year, month, min(today.day, last_day_of_target_month))


def historical_query(months: int = 12, today: date | None = None) -> str:
    """The Gmail search query for "everything from the last N months"."""
    start = twelve_months_ago(today=today, months=months)
    return f"after:{start.strftime('%Y/%m/%d')}"


def run_historical_cleanup(
    status: HistoricalRunStatus,
    *,
    months: int = 12,
    confirm: bool = False,
    use_ai: bool = False,
    read_attachments: bool = False,
    include_contacts: bool = True,
    include_workbook: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_messages: int | None = None,
    page_pause_seconds: float = DEFAULT_PAGE_PAUSE_SECONDS,
    should_cancel: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run the full sweep synchronously, mutating ``status`` in place as it
    goes. Intended to be called inside ``asyncio.to_thread`` — every call
    here (Gmail, Sheets) is synchronous, exactly like every other
    Gmail/Sheets-backed background loop in this app.

    Never raises: every failure path — no connected account, a safety
    invariant violation, anything else — is caught here and reflected in
    ``status.state``/``status.last_error`` rather than propagating to the
    caller, so a bad run can't crash the background task that's running it.
    """
    from app.audit import service as audit_service
    from app.classification import pipeline
    from app.gmail import apply as gmail_apply
    from app.google_api import NotConnectedError
    from app.scheduling.retry import call_with_retry
    from app.sheets.repository import ControlWorkbook

    status.state = "running"
    status.started_at = _now_iso()
    status.months = months
    status.confirm = confirm

    try:
        workbook = ControlWorkbook.connect()
    except NotConnectedError as exc:
        status.state = "not_connected"
        status.last_error = str(exc)
        status.completed_at = _now_iso()
        return

    gate = gmail_apply.check_write_gate(workbook)
    status.gate_allowed = gate.allowed
    status.gate_reasons = list(gate.reasons)
    will_write = confirm and gate.allowed

    run_id = audit_service.new_run_id()
    status.run_id = run_id

    try:
        gmail = _get_gmail_client()
        profile = gmail.get_profile()
        user_email = str(profile.get("emailAddress") or "").lower()
        context = pipeline.build_live_context(
            include_contacts=include_contacts,
            include_workbook=include_workbook,
            user_email=user_email,
            workbook=workbook,
        )

        provider = None
        tracker = None
        if use_ai:
            from app.ai import build_provider
            from app.ai.costs import CostTracker

            provider = build_provider()
            tracker = CostTracker()

        write_client = _get_write_client() if will_write else None

        query = historical_query(months=months)
        page_token: str | None = None
        first_page = True

        while True:
            if should_cancel():
                status.state = "cancelled"
                break

            listing = call_with_retry(
                lambda pt=page_token: gmail.list_message_ids(
                    query=query, max_results=batch_size, page_token=pt
                ),
                description="historical_list_page",
            )
            if first_page:
                status.estimated_total = listing.get("resultSizeEstimate")
                first_page = False

            stubs = listing.get("messages") or []
            if not stubs:
                break

            raw_messages: list[dict] = []
            for stub in stubs:
                if max_messages is not None and status.messages_seen >= max_messages:
                    break
                status.messages_seen += 1
                message_id = stub.get("id")
                try:
                    raw = call_with_retry(
                        lambda mid=message_id: gmail.get_message(mid),
                        description="historical_get_message",
                    )
                    raw_messages.append(raw)
                except Exception as exc:  # noqa: BLE001 — one bad fetch must not stop the sweep
                    status.errors += 1
                    status.last_error = str(exc)
                    log.warning(
                        "historical_message_fetch_failed",
                        extra={"message_id": message_id, "error": str(exc)},
                    )

            results = pipeline.classify_raw_messages(
                raw_messages,
                user_email,
                context,
                gmail,
                use_ai=use_ai,
                read_attachments=read_attachments,
                provider=provider,
                tracker=tracker,
            )

            label_map = None
            if will_write and results:
                label_map = gmail_apply.label_name_map_for(
                    write_client, [r.classification for r in results]
                )

            audit_rows: list[dict[str, str]] = []
            for r in results:
                message, decision = r.message, r.classification
                status.would_review_count += int(decision.review)
                status.protected_count += int(decision.protected)
                try:
                    if will_write:
                        change = gmail_apply.apply_to_message(
                            write_client, message, decision, label_map
                        )
                        if change.changed:
                            status.messages_changed += 1
                            audit_rows.append(
                                audit_service.event_from_applied_change(
                                    change,
                                    subject=message.subject,
                                    classification=", ".join(decision.gmail_label_names)
                                    or "(none)",
                                    priority=decision.priority.value,
                                    confidence=decision.confidence,
                                    reason=decision.rationale,
                                    run_id=run_id,
                                ).as_row()
                            )
                    status.messages_processed += 1
                except Exception as exc:  # noqa: BLE001 — isolate one message's apply failure
                    status.errors += 1
                    status.last_error = str(exc)
                    log.warning(
                        "historical_message_apply_failed",
                        extra={"message_id": message.message_id, "error": str(exc)},
                    )

            if audit_rows:
                workbook.audit_log.record_many(audit_rows)

            status.pages_processed += 1
            log.info(
                "historical_page_processed",
                extra={
                    "run_id": run_id,
                    "pages_processed": status.pages_processed,
                    "messages_seen": status.messages_seen,
                    "messages_changed": status.messages_changed,
                    "errors": status.errors,
                },
            )

            if max_messages is not None and status.messages_seen >= max_messages:
                break

            page_token = listing.get("nextPageToken")
            if not page_token:
                break

            if page_pause_seconds:
                sleep(page_pause_seconds)

        if status.state == "running":
            status.state = "completed"

    except AssertionError as exc:
        # A protected email nearly got routed to Review — a bug in the
        # classifier itself, not a one-off data problem. Stop the whole
        # sweep rather than silently skip the message it happened on;
        # see the module docstring.
        status.state = "failed"
        status.last_error = f"safety invariant violated: {exc}"
        log.error(
            "historical_run_safety_violation",
            extra={"run_id": run_id, "error": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001 — never let the run crash the caller
        status.state = "failed"
        status.last_error = str(exc)
        log.error("historical_run_errored", extra={"run_id": run_id, "error": str(exc)})
    finally:
        status.completed_at = _now_iso()
        workbook.system_runs.record(
            run_id=run_id,
            mode="historical",
            started_at=status.started_at,
            completed_at=status.completed_at,
            emails_processed=status.messages_processed,
            emails_changed=status.messages_changed,
            errors=status.errors,
            undo_available=will_write and status.messages_changed > 0,
        )
        log.info(
            "historical_run_finished",
            extra={
                "run_id": run_id,
                "state": status.state,
                "messages_processed": status.messages_processed,
                "messages_changed": status.messages_changed,
                "errors": status.errors,
            },
        )


def _get_gmail_client():
    from app.gmail.client import get_client

    return get_client()


def _get_write_client():
    from app.gmail.write_client import get_write_client

    return get_write_client()


__all__ = (
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_PAGE_PAUSE_SECONDS",
    "historical_query",
    "run_historical_cleanup",
    "twelve_months_ago",
)
