"""Small shared shapes for the learning layer (CLAUDE.md §11)."""

from __future__ import annotations

from dataclasses import dataclass

#: ``Review_Feedback.user_decision`` values a dashboard action can record.
DECISION_KEPT = "kept"
DECISION_REVIEW_CORRECT = "review_correct"
DECISION_SENDER_RULE_SUGGESTED = "sender_rule_suggested"
DECISION_DOMAIN_RULE_SUGGESTED = "domain_rule_suggested"
DECISION_VIP_SUGGESTED = "vip_suggested"

#: How many times a sender must appear in one classification window before
#: correspondence frequency alone counts as a VIP signal (CLAUDE.md §8).
VIP_FREQUENCY_THRESHOLD = 3


@dataclass(frozen=True)
class FeedbackOutcome:
    """What happened when a dashboard action tried to record a suggestion.

    ``ok=False`` means nothing was written — e.g. a domain rule was refused
    for a public mailbox provider (CLAUDE.md §8). ``message`` is always safe
    to show the user as-is.
    """

    ok: bool
    message: str
    suggestion_id: str | None = None


__all__ = (
    "DECISION_DOMAIN_RULE_SUGGESTED",
    "DECISION_KEPT",
    "DECISION_REVIEW_CORRECT",
    "DECISION_SENDER_RULE_SUGGESTED",
    "DECISION_VIP_SUGGESTED",
    "VIP_FREQUENCY_THRESHOLD",
    "FeedbackOutcome",
)
