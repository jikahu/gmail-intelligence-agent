"""Shapes for the launch quality gate (CLAUDE.md §14 Phase 10, §15).

Two related report types live here:

* :class:`AcceptanceReport` — the result of one 250-email stratified dry run
  against real, connected Gmail.
* :class:`GoldenReport` (in :mod:`app.acceptance.golden`) — the result of
  running the same classifier against a small, hand-labeled dataset with
  known-correct answers, so it doubles as a permanent regression test.

Both report the same headline number: how many protected/important emails
were incorrectly routed to Review. CLAUDE.md §15 is explicit that this must
be exactly zero before live Gmail writes (Phase 11) are ever turned on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Stratum:
    """One category bucket in the stratified sample (CLAUDE.md §15's list).

    ``query`` is a Gmail search expression approximating the category. Gmail
    search can't perfectly target every category CLAUDE.md names — "active
    email conversations" or "known contacts" aren't cleanly query-expressible
    — so this is a deliberate, documented best-effort approximation, not an
    exact partition. See ``app/acceptance/strata.py`` for the full list and
    the honesty note about its limits.
    """

    name: str
    query: str
    target: int
    purpose: str = ""


@dataclass(frozen=True)
class FalseReviewCase:
    """One message the engine both marked protected *and* routed to Review.

    In ordinary operation this can't happen — ``engine._assert_safety_invariants``
    raises before returning such a decision, so a run that reaches this
    dataclass at all already passed that structural check. This type exists
    for the (extremely unlikely, and itself gate-failing) case where a run
    somehow produced one anyway, so it's reported rather than silently lost.
    """

    message_id: str
    thread_id: str
    sender_email: str
    subject_safe_ref: str
    protection_reasons: tuple[str, ...]
    review_reason: str


@dataclass
class AcceptanceReport:
    """The result of one stratified acceptance run."""

    run_id: str
    target_size: int
    sample_size: int
    strata: dict[str, int]
    summary: dict[str, object]
    review_rows: list = field(default_factory=list)  # list[app.dashboard.service.Row]
    false_reviews: list[FalseReviewCase] = field(default_factory=list)
    generated_at: str = field(default_factory=_now_iso)

    @property
    def passed(self) -> bool:
        """The CLAUDE.md §15 gate: zero protected emails wrongly sent to Review."""
        return len(self.false_reviews) == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "target_size": self.target_size,
            "sample_size": self.sample_size,
            "strata": self.strata,
            "summary": self.summary,
            "passed": self.passed,
            "false_reviews": [
                {
                    "message_id": c.message_id,
                    "thread_id": c.thread_id,
                    "sender_email": c.sender_email,
                    "subject": c.subject_safe_ref,
                    "protection_reasons": list(c.protection_reasons),
                    "review_reason": c.review_reason,
                }
                for c in self.false_reviews
            ],
            "review_count": len(self.review_rows),
        }


__all__ = (
    "AcceptanceReport",
    "FalseReviewCase",
    "Stratum",
)
