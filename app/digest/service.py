"""Builds the CLAUDE.md §13 daily digest from the same data the Command
Center already computes (Phase 8) — reordered into the digest's own section
order and narrowed to the sections the digest asks for. Nothing here reads
Gmail directly or writes to it: ``dashboard.service.build_command_center``
already does the one read-only pass this reuses.

:func:`generate_if_due` is the other half — the clock-aware check the
background scheduler (``app/digest/scheduler.py``) calls on a timer. It is
deliberately separate from :func:`build_digest`: a dashboard page load
should always show a fresh digest (the same "recompute, don't read back a
stale snapshot" rule every other Command Center screen follows), but the
*scheduled* midnight digest should only ever fire once per calendar day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.digest.models import DigestReport, DigestSection
from app.logging_config import get_logger

log = get_logger("app.digest.service")

#: CLAUDE.md §13's digest order. Deliberately different from the Command
#: Center's own card order (``dashboard.service.CARD_KEYS``) — Overdue moves
#: ahead of Waiting for Reply and Due Soon, and VIP Suggestions +
#: Subscription Review are dashboard-only, not part of the digest.
DIGEST_SECTION_KEYS: tuple[str, ...] = (
    "p1",
    "p2",
    "action",
    "overdue",
    "waiting",
    "due_soon",
    "review",
)


def digest_timezone(workbook=None) -> ZoneInfo:
    """The configured digest timezone.

    CLAUDE.md §12 lists ``digest_timezone`` as a workbook-editable Setting,
    so the workbook is checked first; env config (what a fresh workbook is
    seeded with anyway) is the fallback when no workbook is available or the
    value is missing/invalid.
    """
    if workbook is not None:
        try:
            raw = workbook.settings.get("digest_timezone")
            if raw:
                return ZoneInfo(raw)
        except Exception as exc:  # noqa: BLE001 — degrade to env config
            log.info(
                "digest_timezone_from_workbook_unavailable", extra={"error": str(exc)}
            )
    return get_settings().digest_tz


def digest_hour(workbook=None) -> int:
    """The configured digest hour (0-23), workbook first, env as the fallback."""
    if workbook is not None:
        try:
            return workbook.settings.get_int("digest_hour", get_settings().digest_hour)
        except Exception as exc:  # noqa: BLE001
            log.info("digest_hour_from_workbook_unavailable", extra={"error": str(exc)})
    return get_settings().digest_hour


def build_digest(
    limit: int | None = None,
    query: str | None = None,
    today: date | None = None,
    tz: ZoneInfo | None = None,
    workbook=None,
) -> DigestReport:
    """Read-only. Runs the same pipeline the Command Center does and keeps
    only the sections + order CLAUDE.md §13 wants for the digest.
    """
    from app.dashboard import service as dashboard_service

    tz = tz or digest_timezone(workbook)
    digest_date = today or datetime.now(tz).date()
    window = limit if limit is not None else dashboard_service.DEFAULT_WINDOW

    center = dashboard_service.build_command_center(
        limit=window, query=query, today=digest_date
    )

    sections: list[DigestSection] = []
    for key in DIGEST_SECTION_KEYS:
        card = center.card(key)
        if card is None:
            continue
        sections.append(
            DigestSection(key=key, title=card.title, blurb=card.blurb, rows=center.rows(key))
        )

    return DigestReport(
        account=center.account,
        digest_date=digest_date,
        timezone=str(tz),
        generated_at=center.generated_at,
        dry_run=center.dry_run,
        sections=sections,
    )


def _row_dict(row) -> dict[str, object]:
    return {
        "message_id": row.message_id,
        "thread_id": row.thread_id,
        "sender_email": row.sender_email,
        "sender_name": row.sender_name,
        "subject": row.subject,
        "received": row.received,
        "snippet": row.snippet,
        "reason": row.reason,
        "confidence": row.confidence,
        "labels": row.labels,
        "priority": row.priority,
        "note": row.note,
    }


def report_as_dict(report: DigestReport) -> dict[str, object]:
    return {
        "account": report.account,
        "digest_date": report.digest_date.isoformat(),
        "timezone": report.timezone,
        "generated_at": report.generated_at.isoformat(),
        "dry_run": report.dry_run,
        "total": report.total,
        "counts": report.counts(),
        "sections": [
            {
                "key": section.key,
                "title": section.title,
                "blurb": section.blurb,
                "count": section.count,
                "rows": [_row_dict(row) for row in section.rows],
            }
            for section in report.sections
        ],
    }


@dataclass
class DigestCheckOutcome:
    """What :func:`generate_if_due` decided to do (or not do) this check."""

    #: "generated" | "already_done" | "not_yet_due"
    result: str
    digest_date: str


def generate_if_due(workbook=None) -> DigestCheckOutcome:
    """Build + persist today's digest exactly once, no earlier than
    ``digest_hour`` in the configured timezone.

    Safe to call as often as you like — ``Digest_Log`` is keyed on date, so a
    call after the day's digest already exists just reports ``already_done``
    and touches nothing further. This is what the background scheduler calls
    on every tick; a manual ``POST /digest/scan`` uses :func:`build_digest`
    directly instead, since a user asking for it explicitly shouldn't have to
    wait for the clock.
    """
    from app.digest import persistence as digest_persistence
    from app.sheets.repository import ControlWorkbook

    if workbook is None:
        workbook = ControlWorkbook.connect()

    tz = digest_timezone(workbook)
    now = datetime.now(tz)
    today_iso = now.date().isoformat()

    if workbook.digest_log.for_date(today_iso) is not None:
        return DigestCheckOutcome(result="already_done", digest_date=today_iso)

    if now.hour < digest_hour(workbook):
        return DigestCheckOutcome(result="not_yet_due", digest_date=today_iso)

    report = build_digest(today=now.date(), tz=tz, workbook=workbook)
    digest_persistence.persist(workbook, report)
    return DigestCheckOutcome(result="generated", digest_date=today_iso)


__all__ = (
    "DIGEST_SECTION_KEYS",
    "DigestCheckOutcome",
    "build_digest",
    "digest_hour",
    "digest_timezone",
    "generate_if_due",
    "report_as_dict",
)
