"""The policy validator — step 9 of CLAUDE.md §11.

**AI suggests. The rules engine decides.**

Everything an AI returns passes through here before it can affect anything. The
validator's contract is one-directional: the AI may make an email *more*
visible or *more* urgent, never less.

| The AI may | The AI may never |
|---|---|
| Add category labels | Route a protected email to Review |
| Raise priority (P3 → P2 → P1) | Route a P1/P2 email to Review |
| Flag an action or a suspicion | Lower a priority the rules already set |
| Supply a summary and a reason | Remove protection |
| | Use a label outside the taxonomy |
| | Cause a Trash, a delete, or any Gmail action |

Low confidence has one effect and one only: it routes the message to Review —
and even that is subject to the protection veto, so an uncertain AI answer about
a bank statement still leaves the statement in the Inbox.

The last thing this module does is re-run the engine's own safety assertions
over the merged result, so an AI-assisted decision is held to exactly the same
invariants as a deterministic one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.ai.base import AIResult
from app.ai.schemas import AISuggestion
from app.classification.engine import (
    Classification,
    _assert_safety_invariants,
)
from app.classification.labels import (
    Label,
    Priority,
    combine_policies,
    most_urgent,
)
from app.classification.message import EmailMessage
from app.classification.signals import Signals
from app.classification.signals import detect as detect_signals
from app.logging_config import get_logger

log = get_logger("app.ai.validator")

#: Confidence at or below this is treated as "the AI doesn't know either".
DEFAULT_REVIEW_THRESHOLD = 0.7

#: Labels the AI may never apply directly.
#:
#: ``AI/Trash-Candidate`` is internal-only and must never reach Gmail (§6).
#: ``AI/Review`` and ``AI/Low-Value`` are *consequences* of the Review decision,
#: not categories the AI gets to assert — otherwise a vetoed Review would still
#: leave the message labelled "Review" while sitting in the Inbox. The AI asks
#: for Review through ``review_reason``; whether it gets it is decided below.
FORBIDDEN_AI_LABELS: frozenset[Label] = frozenset(
    {Label.TRASH_CANDIDATE, Label.REVIEW, Label.LOW_VALUE}
)


@dataclass(frozen=True)
class ValidationOutcome:
    """The merged decision plus a record of what was refused."""

    classification: Classification
    accepted: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()

    @property
    def ai_changed_anything(self) -> bool:
        return bool(self.accepted)


def validate(
    base: Classification,
    result: AIResult,
    message: EmailMessage,
    signals: Signals | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> ValidationOutcome:
    """Merge an AI suggestion into a deterministic decision, safely.

    ``base`` is the engine's own answer and is authoritative on every point of
    conflict. Returns the merged classification; on any failure the base
    decision is returned untouched.
    """
    if not result.succeeded or result.suggestion is None:
        return ValidationOutcome(
            classification=base,
            rejected=(result.describe(),) if not result.was_called or result.error else (),
        )

    suggestion = result.suggestion
    signals = signals if signals is not None else detect_signals(message)

    accepted: list[str] = []
    rejected: list[str] = []

    labels = set(base.labels)
    labels |= _accept_labels(suggestion, labels, accepted, rejected)

    priority = _accept_priority(base, suggestion, accepted, rejected)
    action_required = base.action_required or _accept_action(suggestion, accepted)
    if action_required:
        labels.add(Label.ACTION_REQUIRED)

    review, review_reason = _accept_review(
        base=base,
        suggestion=suggestion,
        priority=priority,
        signals=signals,
        review_threshold=review_threshold,
        accepted=accepted,
        rejected=rejected,
    )

    if review:
        labels.add(Label.REVIEW)
        if not labels - {Label.REVIEW, Label.EXPIRED}:
            labels.add(Label.LOW_VALUE)

    merged = _apply_placement(
        base=base,
        labels=labels,
        priority=priority,
        review=review,
        review_reason=review_reason,
        action_required=action_required,
        suggestion=suggestion,
        result=result,
        accepted=accepted,
    )

    try:
        _assert_safety_invariants(merged, signals)
    except AssertionError as exc:
        # The merge produced something the engine's own rules forbid. Discard
        # the AI's contribution entirely rather than emit an unsafe decision.
        log.error(
            "ai_suggestion_violated_safety_invariant",
            extra={"message_id": base.message_id, "violation": str(exc)},
        )
        return ValidationOutcome(
            classification=base,
            rejected=tuple(rejected) + ("the AI's answer broke a safety rule and was discarded",),
        )

    return ValidationOutcome(
        classification=merged,
        accepted=tuple(accepted),
        rejected=tuple(rejected),
    )


# --------------------------------------------------------------------
# Individual acceptance decisions
# --------------------------------------------------------------------


def _accept_labels(
    suggestion: AISuggestion,
    existing: set[Label],
    accepted: list[str],
    rejected: list[str],
) -> set[Label]:
    """Take the AI's labels, minus anything it isn't allowed to introduce."""
    added: set[Label] = set()
    for label in suggestion.labels:
        if label in FORBIDDEN_AI_LABELS:
            if label is not Label.TRASH_CANDIDATE:
                # Not an error on the AI's part — it's asking for Review, which
                # is handled as a decision rather than as a label.
                continue
            rejected.append(f"AI proposed {label.value}, which it may never apply")
            continue
        if label in existing:
            continue
        added.add(label)

    if added:
        names = ", ".join(sorted(label.value for label in added))
        accepted.append(f"AI added {names}")
    return added


def _accept_priority(
    base: Classification,
    suggestion: AISuggestion,
    accepted: list[str],
    rejected: list[str],
) -> Priority:
    """Priority may only ever move toward urgent."""
    merged = most_urgent(base.priority, suggestion.priority)
    if merged is not base.priority:
        accepted.append(
            f"AI raised priority from {base.priority.value} to {merged.value}"
        )
    elif suggestion.priority.rank > base.priority.rank:
        rejected.append(
            f"AI suggested lowering priority to {suggestion.priority.value}; "
            f"kept {base.priority.value}"
        )
    return merged


def _accept_action(suggestion: AISuggestion, accepted: list[str]) -> bool:
    if suggestion.action_required:
        accepted.append("AI flagged this as needing action")
        return True
    return False


def _accept_review(
    base: Classification,
    suggestion: AISuggestion,
    priority: Priority,
    signals: Signals,
    review_threshold: float,
    accepted: list[str],
    rejected: list[str],
) -> tuple[bool, str | None]:
    """Decide Review, applying every veto the engine applies."""
    review = base.review
    reason = base.review_reason

    wants_review = suggestion.review_reason is not None or Label.REVIEW in suggestion.labels
    low_confidence = suggestion.confidence < review_threshold

    if not review and wants_review:
        review, reason = True, suggestion.review_reason or "the AI judged this low value"
        accepted.append("AI proposed Review")
    elif not review and low_confidence and not base.labels:
        # CLAUDE.md §11: when uncertain → AI/Review, protection rules still apply.
        review = True
        reason = (
            f"neither the rules nor the AI could classify this confidently "
            f"({suggestion.confidence:.0%})"
        )
        accepted.append("low confidence routed this to Review")

    if not review:
        return False, reason

    # --- The vetoes. Identical to the engine's, deliberately duplicated here
    # --- so an AI-proposed Review is held to the same standard as a rule one.
    if base.protected and not signals.is_suspicious:
        protection = base.protection_reasons[0] if base.protection_reasons else "protected"
        rejected.append(
            f"AI proposed Review but this email is protected ({protection})"
        )
        return False, None

    if priority is not Priority.P3_NORMAL and not signals.is_suspicious:
        rejected.append(
            f"AI proposed Review but this email is {priority.value}"
        )
        return False, None

    return True, reason


def _apply_placement(
    base: Classification,
    labels: set[Label],
    priority: Priority,
    review: bool,
    review_reason: str | None,
    action_required: bool,
    suggestion: AISuggestion,
    result: AIResult,
    accepted: list[str],
) -> Classification:
    """Recompute where the message goes, using the engine's own rules."""
    policy = combine_policies(labels) if labels else combine_policies({Label.PERSONAL})

    keep_in_inbox = policy.keep_in_inbox
    archive = policy.archive
    if priority is Priority.P1_URGENT and not review:
        keep_in_inbox, archive = True, False

    mark_important = policy.mark_important or priority is Priority.P1_URGENT
    if (
        labels == {Label.PERSONAL}
        and priority is Priority.P3_NORMAL
        and not action_required
    ):
        mark_important = False

    rationale = base.rationale
    if suggestion.safe_rationale():
        rationale = suggestion.safe_rationale()

    triggered = list(base.rules_triggered)
    triggered.extend(f"AI: {note}" for note in accepted)
    triggered.append(
        f"AI consulted ({result.usage.provider}/{result.usage.model}, "
        f"prompt {result.prompt_version}, confidence {suggestion.confidence:.2f})"
    )

    return replace(
        base,
        labels=labels,
        priority=priority,
        keep_in_inbox=keep_in_inbox and not review,
        archive=archive or review,
        mark_important=mark_important,
        review=review,
        review_reason=review_reason,
        action_required=action_required,
        confidence=max(base.confidence, suggestion.confidence),
        rules_triggered=tuple(triggered),
        needs_ai=False,
        rationale=rationale,
    )


__all__ = (
    "DEFAULT_REVIEW_THRESHOLD",
    "FORBIDDEN_AI_LABELS",
    "ValidationOutcome",
    "validate",
)
