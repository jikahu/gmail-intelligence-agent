"""One real-time poll cycle (CLAUDE.md §13): find new mail since the last
poll, classify it with full thread context, and — only if the write gate
allows it — apply the result to Gmail. This is the bounded, automatic
counterpart to :mod:`app.gmail.write_service`, which is the manual,
user-triggered version of the same "classify, then maybe apply" shape; both
ultimately go through the same :mod:`app.gmail.apply` gate and diff logic.

**Thread-aware classification.** Every other read-only entry point in this
app (``/classify/preview``, the acceptance run, the dashboard) classifies
messages fetched individually, so ``EmailMessage.thread_message_count`` /
``user_in_thread`` default to "just this one message" — a known, documented
gap since Phase 7 ("full-thread pulls come with the dashboard/real-time
phases"). This module is that phase: a new message's *whole* thread is
fetched in one call (:meth:`app.gmail.client.GmailReadClient.get_thread_full`)
so CLAUDE.md §8's "active email conversations" protection sees the real
thread state, not a single message in isolation.

**Only new messages are reclassified, never the rest of the thread.** Fetching
the whole thread is for *context* only — it does not mean every older message
in that thread gets reprocessed and potentially re-labelled. An explicit user
correction on an older message (Restore, Make Sender Rule, ...) must not be
silently fought by the next reply arriving in the same thread (CLAUDE.md §11:
explicit user decisions outrank behavioral inference).

**Idempotent by construction.** :func:`app.gmail.apply.plan_change` computes
an empty plan for a message already in its desired state, so reprocessing the
same message twice (a retried cycle, an overlapping history page) writes
nothing and logs nothing the second time. "Avoid endless reclassification"
follows from that plus the history cursor only ever moving forward — Gmail
never hands this app the same history record twice.

**Failures are per-message, not per-cycle.** One bad thread fetch or one
failed ``modify`` call is logged and skipped; it never aborts the rest of the
cycle, and it never gets stuck retrying forever — once the cursor advances
past a historyId, that record does not come back on the next poll (see
:mod:`app.scheduling.history`), so a permanently-broken message is reported
once, not endlessly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.audit import service as audit_service
from app.classification.engine import classify
from app.classification.message import EmailMessage, from_gmail_thread
from app.gmail import apply as gmail_apply
from app.logging_config import get_logger
from app.scheduling import history as history_mod
from app.scheduling.retry import call_with_retry

log = get_logger("app.scheduling.poller")

#: The Settings key the mailbox's last-seen history id is stored under. Lives
#: in the control workbook, not env config — it is per-connected-account
#: operational state, the same reason ``last_acceptance_passed`` lives there.
HISTORY_CURSOR_KEY = "real_time_last_history_id"

RUN_MODE = "real_time"


@dataclass(frozen=True)
class ProcessedMessage:
    """One message's outcome for this cycle — for the JSON report only; the
    durable record is the Audit_Log rows written alongside it."""

    message_id: str
    thread_id: str
    subject: str
    labels: tuple[str, ...] = ()
    changed: bool = False
    action_taken: str = ""
    error: str = ""


@dataclass(frozen=True)
class PollReport:
    #: True when this was the very first poll for this workbook — nothing
    #: was processed, only the starting cursor was recorded.
    bootstrapped: bool
    #: True when the stored cursor had expired and had to be reset to "now" —
    #: mail from the gap was not seen by this scan.
    history_gap: bool
    gate_allowed: bool
    gate_reasons: tuple[str, ...]
    messages_seen: int
    messages_processed: int
    changed_count: int
    error_count: int
    processed: tuple[ProcessedMessage, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "bootstrapped": self.bootstrapped,
            "history_gap": self.history_gap,
            "gate_allowed": self.gate_allowed,
            "gate_reasons": list(self.gate_reasons),
            "messages_seen": self.messages_seen,
            "messages_processed": self.messages_processed,
            "changed_count": self.changed_count,
            "error_count": self.error_count,
            "processed": [
                {
                    "id": m.message_id,
                    "thread_id": m.thread_id,
                    "subject": m.subject,
                    "labels": list(m.labels),
                    "changed": m.changed,
                    "action_taken": m.action_taken,
                    "error": m.error,
                }
                for m in self.processed
            ],
        }


def _bootstrap(workbook, gmail, gate) -> PollReport:
    """First-ever poll for this workbook: record "now" as the starting point
    without processing anything.

    Deliberately conservative — the same reason every other automatic
    behavior in this app defaults closed until turned on — so switching real-
    time processing on for the first time never sweeps through however many
    messages already happen to be sitting in the mailbox. A deliberate
    one-time catch-up is ``/gmail/apply`` or ``/acceptance/run``'s job; a full
    12-month sweep is the future Phase 15's.
    """
    baseline = history_mod.current_history_id(gmail)
    workbook.settings.set(
        HISTORY_CURSOR_KEY,
        baseline,
        description="Real-time poller's last-seen Gmail history id.",
    )
    log.info("realtime_poll_bootstrapped", extra={"history_id": baseline})
    return PollReport(
        bootstrapped=True,
        history_gap=False,
        gate_allowed=gate.allowed,
        gate_reasons=gate.reasons,
        messages_seen=0,
        messages_processed=0,
        changed_count=0,
        error_count=0,
    )


def _thread_messages(gmail, user_email: str, thread_id: str) -> list[EmailMessage]:
    def _fetch() -> dict:
        return gmail.get_thread_full(thread_id)

    raw_thread = call_with_retry(_fetch, description="threads.get")
    return from_gmail_thread(raw_thread, user_email=user_email)


def run_poll_cycle(workbook=None, use_ai: bool = True) -> PollReport:
    """Run exactly one poll cycle.

    Safe to call on a timer (:mod:`app.scheduling.service`) or by hand
    (``POST /realtime/poll``) — both share this one implementation, so there
    is only one real code path to trust. Raises ``NotConnectedError`` the same
    way every other Gmail-backed entry point does; callers on a timer are
    expected to catch that and simply try again next cycle.
    """
    from app.ai import assist, build_provider
    from app.ai.costs import CostTracker
    from app.classification.pipeline import PreviewResult, build_live_context
    from app.gmail.client import get_client as get_gmail_client
    from app.gmail.write_client import get_write_client
    from app.sheets.repository import ControlWorkbook

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    workbook = workbook or ControlWorkbook.connect()
    gmail = get_gmail_client()
    gate = gmail_apply.check_write_gate(workbook)

    cursor = workbook.settings.get(HISTORY_CURSOR_KEY)
    if not cursor:
        return _bootstrap(workbook, gmail, gate)

    scan = history_mod.scan_for_changes(gmail, cursor)
    workbook.settings.set(
        HISTORY_CURSOR_KEY,
        scan.new_history_id,
        description="Real-time poller's last-seen Gmail history id.",
    )

    if not scan.messages:
        return PollReport(
            bootstrapped=False,
            history_gap=scan.history_gap,
            gate_allowed=gate.allowed,
            gate_reasons=gate.reasons,
            messages_seen=0,
            messages_processed=0,
            changed_count=0,
            error_count=0,
        )

    profile = gmail.get_profile()
    user_email = str(profile.get("emailAddress") or "").lower()
    context = build_live_context(user_email=user_email, workbook=workbook)

    provider = build_provider() if use_ai else None
    tracker = CostTracker()
    write_client = get_write_client() if gate.allowed else None

    # Group by thread so each thread is fetched once even when more than one
    # of its messages appears in this cycle's changes.
    by_thread: dict[str, list[str]] = {}
    for changed in scan.messages:
        by_thread.setdefault(changed.thread_id, []).append(changed.message_id)

    run_id = audit_service.new_run_id()
    audit_rows: list[dict[str, str]] = []
    processed: list[ProcessedMessage] = []
    changed_count = 0
    error_count = 0

    for thread_id, message_ids in by_thread.items():
        try:
            thread_messages = _thread_messages(gmail, user_email, thread_id)
        except Exception as exc:  # noqa: BLE001 — one bad thread must not stop the cycle
            log.warning(
                "realtime_thread_fetch_failed",
                extra={"thread_id": thread_id, "error": str(exc)},
            )
            error_count += len(message_ids)
            processed.extend(
                ProcessedMessage(
                    message_id=message_id, thread_id=thread_id, subject="", error=str(exc)
                )
                for message_id in message_ids
            )
            continue

        by_id = {m.message_id: m for m in thread_messages}
        for message_id in message_ids:
            message = by_id.get(message_id)
            if message is None:
                # Gone already — e.g. deleted moments after arriving.
                processed.append(
                    ProcessedMessage(
                        message_id=message_id,
                        thread_id=thread_id,
                        subject="",
                        action_taken="skipped — no longer in the thread",
                    )
                )
                continue
            if message.sent_by_user:
                # The user's own outgoing copy also raises a messageAdded
                # event; it is not something the rules engine should label.
                processed.append(
                    ProcessedMessage(
                        message_id=message_id,
                        thread_id=thread_id,
                        subject=message.subject,
                        action_taken="skipped — sent by the user",
                    )
                )
                continue

            decision = classify(message, context)
            if provider is not None and decision.needs_ai:
                outcome = assist(message, decision, provider, tracker=tracker)
                decision = outcome.classification

            try:
                if gate.allowed:
                    label_map = gmail_apply.label_name_map_for(write_client, [decision])

                    def _apply() -> gmail_apply.AppliedChange:
                        return gmail_apply.apply_to_message(
                            write_client, message, decision, label_map
                        )

                    change = call_with_retry(_apply, description="messages.modify")
                    if change.changed:
                        changed_count += 1
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
                    processed.append(
                        ProcessedMessage(
                            message_id=message_id,
                            thread_id=thread_id,
                            subject=message.subject,
                            labels=tuple(decision.gmail_label_names),
                            changed=change.changed,
                            action_taken=change.action_taken,
                        )
                    )
                else:
                    # Gate closed: still classify and log a proposal, the same
                    # dry-run shape /audit/scan already uses, so the user can
                    # see what real-time processing *would* do before turning
                    # live writes on.
                    audit_rows.append(
                        audit_service.event_from_result(
                            PreviewResult(message=message, classification=decision),
                            run_id=run_id,
                        ).as_row()
                    )
                    processed.append(
                        ProcessedMessage(
                            message_id=message_id,
                            thread_id=thread_id,
                            subject=message.subject,
                            labels=tuple(decision.gmail_label_names),
                            action_taken="proposed only — real-time writes are gated closed",
                        )
                    )
            except Exception as exc:  # noqa: BLE001 — log and move on, never crash the cycle
                error_count += 1
                log.warning(
                    "realtime_message_processing_failed",
                    extra={"message_id": message_id, "error": str(exc)},
                )
                processed.append(
                    ProcessedMessage(
                        message_id=message_id,
                        thread_id=thread_id,
                        subject=message.subject,
                        error=str(exc),
                    )
                )

    if audit_rows:
        workbook.audit_log.record_many(audit_rows)

    if gate.allowed:
        workbook.system_runs.record(
            run_id=run_id,
            mode=RUN_MODE,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            emails_processed=len(processed),
            emails_changed=changed_count,
            errors=error_count,
            undo_available=changed_count > 0,
        )

    log.info(
        "realtime_poll_cycle_completed",
        extra={
            "run_id": run_id,
            "messages_seen": len(scan.messages),
            "messages_processed": len(processed),
            "changed_count": changed_count,
            "error_count": error_count,
            "gate_allowed": gate.allowed,
            "ai_calls": tracker.call_count,
        },
    )

    return PollReport(
        bootstrapped=False,
        history_gap=scan.history_gap,
        gate_allowed=gate.allowed,
        gate_reasons=gate.reasons,
        messages_seen=len(scan.messages),
        messages_processed=len(processed),
        changed_count=changed_count,
        error_count=error_count,
        processed=tuple(processed),
    )


__all__ = ("HISTORY_CURSOR_KEY", "PollReport", "ProcessedMessage", "run_poll_cycle")
