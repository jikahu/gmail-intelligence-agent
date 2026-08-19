"""The 250-email stratified dry run (CLAUDE.md §14 Phase 10, §15).

Three steps, all read-only against Gmail:

1. :func:`build_stratified_sample` — pull a deliberately mixed sample using
   the category searches in :mod:`app.acceptance.strata`, deduplicated by
   message id, topped up toward the target from plain recent mail if any
   stratum came up short.
2. :func:`run_acceptance_test` — classify the sample with the same
   deterministic-rules-then-AI pipeline every other phase uses
   (``pipeline.classify_raw_messages``) and build an
   :class:`~app.acceptance.models.AcceptanceReport`.
3. :func:`persist_report` — write the run to the control workbook
   (``System_Runs``, ``Audit_Log``, and a ``Settings`` gate flag Phase 11
   can check later). A separate, explicit step, called from the route —
   this module never connects to the workbook itself, matching how every
   other scan-style service in this app works.

Nothing here writes to Gmail. Nothing here can pass the gate on the app's
say-so alone: :attr:`AcceptanceReport.passed` is just a count. CLAUDE.md §15
still requires a human to look at the sample — especially the emails the
engine did *not* mark protected — before trusting the number.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.acceptance.models import AcceptanceReport, FalseReviewCase
from app.acceptance.strata import DEFAULT_SAMPLE_TARGET, STRATA
from app.logging_config import get_logger

log = get_logger("app.acceptance.service")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

__all__ = (
    "build_stratified_sample",
    "get_report",
    "latest_report",
    "persist_report",
    "reset_cache",
    "run_acceptance_test",
)

# --------------------------------------------------------------------
# In-memory report cache
#
# A report is only meaningful for the run that produced it — re-fetching
# "recent mail" later would pull a different sample. Keeping the last few
# runs in process memory (not a database) is enough for a single-user,
# single-process app to review a run's result after the POST that ran it;
# the durable record is System_Runs + Audit_Log, written by persist_report.
# --------------------------------------------------------------------

_MAX_CACHED_REPORTS = 5
_reports: dict[str, AcceptanceReport] = {}
_report_order: list[str] = []


def _cache_report(report: AcceptanceReport) -> None:
    _reports[report.run_id] = report
    _report_order.append(report.run_id)
    while len(_report_order) > _MAX_CACHED_REPORTS:
        stale_id = _report_order.pop(0)
        _reports.pop(stale_id, None)


def get_report(run_id: str) -> AcceptanceReport | None:
    return _reports.get(run_id)


def latest_report() -> AcceptanceReport | None:
    if not _report_order:
        return None
    return _reports.get(_report_order[-1])


def reset_cache() -> None:
    """Clear the in-memory report cache. Mainly useful for test isolation."""
    _reports.clear()
    _report_order.clear()


# --------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------


def _scaled_targets(target_total: int) -> list[tuple]:
    if target_total == DEFAULT_SAMPLE_TARGET:
        return [(s, s.target) for s in STRATA]
    ratio = target_total / DEFAULT_SAMPLE_TARGET
    return [(s, max(1, round(s.target * ratio))) for s in STRATA]


def build_stratified_sample(
    gmail, target_total: int = DEFAULT_SAMPLE_TARGET
) -> tuple[list[dict], dict[str, int]]:
    """Return ``(raw_messages, achieved_counts)`` — a deduplicated, mixed sample."""
    seen_ids: set[str] = set()
    sample: list[dict] = []
    achieved: dict[str, int] = {}

    for stratum, want in _scaled_targets(target_total):
        raw = gmail.list_recent_messages(max_results=want, query=stratum.query or None)
        count = 0
        for msg in raw:
            message_id = msg.get("id")
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.add(message_id)
            sample.append(msg)
            count += 1
        achieved[stratum.name] = count

    if len(sample) < target_total:
        shortfall = target_total - len(sample)
        raw = gmail.list_recent_messages(max_results=shortfall + len(seen_ids), query=None)
        topped_up = 0
        for msg in raw:
            if len(sample) >= target_total:
                break
            message_id = msg.get("id")
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.add(message_id)
            sample.append(msg)
            topped_up += 1
        if topped_up:
            achieved["top_up"] = topped_up

    return sample[:target_total], achieved


# --------------------------------------------------------------------
# The run itself
# --------------------------------------------------------------------


def _row_from_result(result):
    """A minimal :class:`app.dashboard.service.Row` for the review-list view.

    Deliberately not importing the dashboard's private row builder — this
    report needs fewer fields (no follow-up/intelligence note) and the two
    packages otherwise have no reason to depend on each other.
    """
    from app.dashboard.service import Row

    message, decision = result.message, result.classification
    return Row(
        message_id=message.message_id,
        thread_id=message.thread_id,
        sender_email=message.sender_email,
        sender_name=message.sender_name or message.sender_email,
        subject=message.subject or "(no subject)",
        received=message.date.isoformat() if message.date else None,
        snippet=message.snippet,
        reason=decision.review_reason or decision.rationale,
        confidence=decision.confidence,
        labels=decision.gmail_label_names,
        has_attachments=message.has_attachments,
        priority=decision.priority.value,
    )


def _build_report(results, strata_counts: dict[str, int], target_total: int) -> AcceptanceReport:
    from app.audit.models import new_run_id, safe_subject_ref
    from app.classification.pipeline import summarize

    false_reviews = [
        FalseReviewCase(
            message_id=r.message.message_id,
            thread_id=r.message.thread_id,
            sender_email=r.message.sender_email,
            subject_safe_ref=safe_subject_ref(r.message.subject),
            protection_reasons=r.classification.protection_reasons,
            review_reason=r.classification.review_reason or "",
        )
        for r in results
        if r.classification.protected and r.classification.review
    ]
    review_rows = [_row_from_result(r) for r in results if r.classification.review]

    return AcceptanceReport(
        run_id=new_run_id(),
        target_size=target_total,
        sample_size=len(results),
        strata=strata_counts,
        summary=summarize(results),
        review_rows=review_rows,
        false_reviews=false_reviews,
    )


def run_acceptance_test(
    target_total: int = DEFAULT_SAMPLE_TARGET,
    use_ai: bool = True,
    read_attachments: bool = True,
    include_contacts: bool = True,
    include_workbook: bool = True,
    provider=None,
    tracker=None,
) -> tuple[AcceptanceReport, list]:
    """Run the stratified sample through the full classification pipeline.

    Returns ``(report, results)`` — callers that want to persist the run need
    ``results`` too (for the per-message Audit_Log rows), which the cached
    report alone doesn't retain.
    """
    from app.ai import build_provider
    from app.ai.costs import CostTracker
    from app.classification import pipeline
    from app.gmail.client import get_client as get_gmail_client

    gmail = get_gmail_client()
    profile = gmail.get_profile()
    user_email = str(profile.get("emailAddress") or "").lower()

    context = pipeline.build_live_context(
        include_contacts=include_contacts,
        include_workbook=include_workbook,
        user_email=user_email,
    )

    if use_ai and provider is None:
        provider = build_provider()
    tracker = tracker if tracker is not None else CostTracker()

    sample, strata_counts = build_stratified_sample(gmail, target_total=target_total)
    results = pipeline.classify_raw_messages(
        sample,
        user_email,
        context,
        gmail,
        use_ai=use_ai,
        read_attachments=read_attachments,
        provider=provider,
        tracker=tracker,
    )

    report = _build_report(results, strata_counts, target_total)
    _cache_report(report)

    log.info(
        "acceptance_run_completed",
        extra={
            "run_id": report.run_id,
            "sample_size": report.sample_size,
            "target_size": report.target_size,
            "review_count": len(report.review_rows),
            "false_reviews": len(report.false_reviews),
            "passed": report.passed,
        },
    )
    return report, results


# --------------------------------------------------------------------
# Persistence — explicit, separate from running the test
# --------------------------------------------------------------------


def persist_report(workbook, report: AcceptanceReport, results, started_at: str) -> None:
    """Write the run to the control workbook. Never touches Gmail.

    Records the same per-message Audit_Log detail every classification run
    can (CLAUDE.md §13), one System_Runs summary row, and three Settings
    flags — ``last_acceptance_run_id``, ``last_acceptance_passed``,
    ``last_acceptance_at`` — the concrete, checkable gate a future Phase 11
    can read before allowing live Gmail writes.
    """
    from app.audit import service as audit_service

    completed_at = _now_iso()

    audit_service.record_run(workbook, results, run_id=report.run_id)

    workbook.system_runs.record(
        run_id=report.run_id,
        mode="dry_run",
        started_at=started_at,
        completed_at=completed_at,
        emails_processed=report.sample_size,
        emails_changed=0,
        errors=0,
        undo_available=False,
    )

    workbook.settings.set(
        "last_acceptance_run_id",
        report.run_id,
        description="The most recent 250-email acceptance run (CLAUDE.md §15).",
    )
    workbook.settings.set(
        "last_acceptance_passed",
        "true" if report.passed else "false",
        description=(
            "Whether the last acceptance run had zero protected-email false "
            "Reviews. Phase 11 must not enable live Gmail writes while this is false."
        ),
    )
    workbook.settings.set(
        "last_acceptance_at",
        completed_at,
        description="When the last acceptance run completed.",
    )

    log.info(
        "acceptance_run_persisted",
        extra={"run_id": report.run_id, "passed": report.passed},
    )
