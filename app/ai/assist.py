"""Deciding when to spend an AI call, and what to do with the answer.

This is step 8 of CLAUDE.md §11, wrapped in the cost discipline of §3: hard
rules first, AI only for what's genuinely unresolved.

The gate is deliberately strict. A message is only sent to a provider when the
deterministic engine has said, in its own output, that it couldn't settle the
question (``needs_ai``). Everything else — the receipts, the statements, the
promotions the rules already recognise — costs nothing.

Two things also stop a call that would otherwise happen:

* **Prompt-injection markers.** A message trying to talk to the model doesn't
  get to talk to the model (CLAUDE.md §16).
* **No configured provider.** The classification simply stays deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai import sanitize
from app.ai.base import AIProvider, AIResult
from app.ai.costs import CostTracker
from app.ai.validator import DEFAULT_REVIEW_THRESHOLD, ValidationOutcome, validate
from app.classification.engine import Classification
from app.classification.message import EmailMessage
from app.classification.signals import Signals
from app.classification.signals import detect as detect_signals
from app.logging_config import get_logger

log = get_logger("app.ai.assist")


@dataclass
class AssistOutcome:
    """What happened when we considered consulting the AI about one email."""

    classification: Classification
    result: AIResult | None = None
    accepted: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()

    @property
    def ai_was_called(self) -> bool:
        return self.result is not None and self.result.was_called

    @property
    def ai_helped(self) -> bool:
        return bool(self.accepted)

    def as_dict(self) -> dict[str, object]:
        """JSON-safe view for the preview page. Never includes email content."""
        if self.result is None:
            return {"consulted": False, "reason": "the rules settled this on their own"}
        return {
            "consulted": self.ai_was_called,
            "provider": self.result.usage.provider,
            "model": self.result.usage.model,
            "prompt_version": self.result.prompt_version,
            "confidence": (
                self.result.suggestion.confidence if self.result.suggestion else None
            ),
            "summary": (
                self.result.suggestion.summary if self.result.suggestion else None
            ),
            "outcome": self.result.describe(),
            "accepted": list(self.accepted),
            "rejected": list(self.rejected),
            "tokens": self.result.usage.total_tokens,
            "estimated_cost_usd": round(self.result.usage.estimated_cost_usd, 6),
        }


def should_consult(base: Classification) -> bool:
    """Only ask the AI about what the rules couldn't settle."""
    return base.needs_ai and not base.sent_by_user


def assist(
    message: EmailMessage,
    base: Classification,
    provider: AIProvider,
    signals: Signals | None = None,
    tracker: CostTracker | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
    force: bool = False,
) -> AssistOutcome:
    """Consult the AI if warranted, validate the answer, and merge it.

    ``force=True`` consults even when the rules were confident — used only by
    diagnostics, and flagged in the cost record as an avoidable call.
    """
    if not force and not should_consult(base):
        return AssistOutcome(classification=base)

    scan = sanitize.scan_for_injection(message)
    if scan.detected:
        log.warning(
            "ai_skipped_prompt_injection",
            extra={
                "message_id": base.message_id,
                "markers": list(scan.markers),
            },
        )
        result = AIResult.skipped(provider.name, provider.model, scan.reason)
        return AssistOutcome(
            classification=base,
            result=result,
            rejected=("the AI was not consulted because " + scan.reason,),
        )

    result = provider.classify_email(message, deterministic=base)
    result.usage.could_have_used_rule = force and not base.needs_ai
    if tracker is not None and result.was_called:
        tracker.record(result.usage)

    signals = signals if signals is not None else detect_signals(message)
    outcome: ValidationOutcome = validate(
        base=base,
        result=result,
        message=message,
        signals=signals,
        review_threshold=review_threshold,
    )

    if outcome.rejected:
        log.info(
            "ai_suggestion_partially_rejected",
            extra={"message_id": base.message_id, "rejected": list(outcome.rejected)},
        )

    return AssistOutcome(
        classification=outcome.classification,
        result=result,
        accepted=outcome.accepted,
        rejected=outcome.rejected,
    )


__all__ = ("AssistOutcome", "assist", "should_consult")
