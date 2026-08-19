"""Maps a :class:`~app.digest.models.DigestReport` to a ``Digest_Log`` row
(CLAUDE.md §12/§13/§14).

Kept separate from ``app/digest/service.py`` the same way
``app/intelligence/persistence.py`` is kept separate from its own service
module — the builder stays pure and Sheets-free; only this module touches
the workbook.
"""

from __future__ import annotations

from app.digest.models import DigestReport


def persist(workbook, report: DigestReport) -> dict[str, object]:
    """Record that this digest ran, with its section counts, in ``Digest_Log``.

    Idempotent per calendar date — see :class:`app.sheets.repository.DigestRepository`.
    """
    action = workbook.digest_log.record(
        digest_date=report.digest_date.isoformat(),
        generated_at=report.generated_at.isoformat(),
        timezone=report.timezone,
        account=report.account,
        counts=report.counts(),
        total=report.total,
    )
    return {"action": action, "digest_date": report.digest_date.isoformat()}


__all__ = ("persist",)
