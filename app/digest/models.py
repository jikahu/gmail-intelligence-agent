"""What one daily digest looks like (CLAUDE.md §13, §14).

A digest is the Command Center's own data (Phase 8), reordered and narrowed
to the sections CLAUDE.md §13 names for the midnight digest — P1, P2, Action
Required, Overdue, Waiting for Reply, Due Soon, and AI Review — and stamped
with the calendar date it covers in the configured digest timezone. It
carries no data the dashboard doesn't already compute; see
``app/digest/service.py`` for how one gets built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.dashboard.service import Row


@dataclass
class DigestSection:
    """One section of the digest — a title, a blurb, and the rows in it."""

    key: str
    title: str
    blurb: str
    rows: list[Row] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)


@dataclass
class DigestReport:
    """One digest run, for one calendar date."""

    account: str
    digest_date: date
    timezone: str
    generated_at: datetime
    dry_run: bool
    sections: list[DigestSection] = field(default_factory=list)

    def section(self, key: str) -> DigestSection | None:
        for section in self.sections:
            if section.key == key:
                return section
        return None

    @property
    def total(self) -> int:
        return sum(section.count for section in self.sections)

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    def counts(self) -> dict[str, int]:
        return {section.key: section.count for section in self.sections}


__all__ = ("DigestReport", "DigestSection")
