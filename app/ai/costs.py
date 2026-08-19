"""Token and cost accounting (CLAUDE.md §17).

Two things are tracked, and the second matters more than the first:

* What each AI call cost.
* Whether a deterministic rule could have answered it for free.

The second number is the one that tells you whether the rules engine is
carrying its weight. If AI calls that a hard rule could have handled keep
showing up, the fix is a rule, not a bigger budget.

Prices are per million tokens, in USD, and are a **local cache** — they are not
fetched at runtime and can go stale. They are used for estimates on the
dashboard, never for billing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.logging_config import get_logger

log = get_logger("app.ai.costs")

#: USD per million tokens, as (input, output). Verified 2026-06-24 for the
#: Anthropic models; OpenAI figures are indicative and should be checked
#: against current OpenAI pricing before being relied on.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI — indicative only.
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

_TOKENS_PER_MILLION = 1_000_000.0


def price_for(model: str) -> tuple[float, float] | None:
    """Return ``(input_per_million, output_per_million)`` or ``None``."""
    return MODEL_PRICES.get((model or "").strip().lower())


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for one call. Unknown model → 0.0 with a warning."""
    prices = price_for(model)
    if prices is None:
        log.warning("ai_model_price_unknown", extra={"ai_model": model})
        return 0.0
    input_price, output_price = prices
    return (
        (max(0, input_tokens) / _TOKENS_PER_MILLION) * input_price
        + (max(0, output_tokens) / _TOKENS_PER_MILLION) * output_price
    )


@dataclass
class AIUsage:
    """What one AI call consumed."""

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    #: e.g. "classification" or "summary".
    classification_type: str = "classification"
    #: True when the deterministic rules had already settled this message and
    #: the AI call was therefore avoidable. The number to watch (§17).
    could_have_used_rule: bool = False
    prompt_version: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def priced(
        cls,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        **kwargs: object,
    ) -> "AIUsage":
        """Build a usage record with the cost already worked out."""
        return cls(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimate_cost(model, input_tokens, output_tokens),
            **kwargs,  # type: ignore[arg-type]
        )


@dataclass
class CostTracker:
    """Accumulates usage across a run. In-memory; persisted in Phase 9."""

    records: list[AIUsage] = field(default_factory=list)

    def record(self, usage: AIUsage) -> AIUsage:
        self.records.append(usage)
        return usage

    @property
    def call_count(self) -> int:
        return len(self.records)

    @property
    def total_cost_usd(self) -> float:
        return sum(record.estimated_cost_usd for record in self.records)

    @property
    def total_tokens(self) -> int:
        return sum(record.total_tokens for record in self.records)

    @property
    def avoidable_calls(self) -> int:
        """Calls a deterministic rule could have handled for free."""
        return sum(1 for record in self.records if record.could_have_used_rule)

    def summary(self) -> dict[str, object]:
        """A compact view for the dashboard and logs."""
        by_model: dict[str, int] = {}
        for record in self.records:
            by_model[record.model] = by_model.get(record.model, 0) + 1
        return {
            "ai_calls": self.call_count,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.total_cost_usd, 6),
            "avoidable_calls": self.avoidable_calls,
            "calls_by_model": by_model,
        }

    def reset(self) -> None:
        self.records.clear()


__all__ = (
    "AIUsage",
    "CostTracker",
    "MODEL_PRICES",
    "estimate_cost",
    "price_for",
)
