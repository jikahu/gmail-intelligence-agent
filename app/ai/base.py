"""The provider-agnostic AI interface (CLAUDE.md §3).

Everything above this layer talks to :class:`AIProvider`. No vendor SDK is
imported outside its own provider module, so switching between Anthropic and
OpenAI is a config change, not a code change.

A provider's job is narrow: turn one email into one :class:`AIResult`. It never
decides anything — :mod:`app.ai.validator` does that. A provider that fails,
times out, refuses, or returns nonsense returns a result carrying the error
rather than raising, because a broken AI call must degrade the classification,
never break the run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.ai.costs import AIUsage
from app.ai.schemas import AISuggestion
from app.classification.engine import Classification
from app.classification.message import EmailMessage


@dataclass
class AIResult:
    """The outcome of one AI consultation."""

    usage: AIUsage
    suggestion: AISuggestion | None = None
    #: Set when the call failed. The classification proceeds without AI help.
    error: str | None = None
    #: True when the provider's safety systems declined the request.
    refused: bool = False
    #: Set when we chose not to call at all (injection detected, no key, …).
    skipped_reason: str | None = None
    prompt_version: str = ""

    @property
    def succeeded(self) -> bool:
        return self.suggestion is not None and self.error is None

    @property
    def was_called(self) -> bool:
        """False when no request left the machine."""
        return self.skipped_reason is None

    def describe(self) -> str:
        if self.skipped_reason:
            return f"AI not consulted: {self.skipped_reason}"
        if self.refused:
            return "The AI provider declined to answer this one."
        if self.error:
            return f"AI unavailable: {self.error}"
        if self.suggestion is not None:
            return self.suggestion.safe_rationale()
        return "The AI returned nothing usable."

    @classmethod
    def skipped(cls, provider: str, model: str, reason: str) -> "AIResult":
        return cls(
            usage=AIUsage(provider=provider, model=model),
            skipped_reason=reason,
        )

    @classmethod
    def failed(cls, provider: str, model: str, error: str) -> "AIResult":
        return cls(usage=AIUsage(provider=provider, model=model), error=error)


@dataclass
class ProviderConfig:
    """Everything a provider needs to run. Sourced from config or the workbook."""

    model: str
    api_key: str | None = None
    max_output_tokens: int = 1024
    timeout_seconds: float = 30.0
    #: Effort/quality hint. Providers map this onto their own knobs.
    effort: str = "low"
    extra: dict[str, object] = field(default_factory=dict)


class AIProvider(ABC):
    """One AI vendor, behind a stable interface."""

    #: Short identifier used in logs, costs, and the workbook.
    name: str = "abstract"

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def is_configured(self) -> bool:
        """False when the provider has no credentials and cannot be called."""
        return bool(self.config.api_key)

    @abstractmethod
    def classify_email(
        self,
        message: EmailMessage,
        deterministic: Classification | None = None,
    ) -> AIResult:
        """Suggest a classification for one email. Never raises."""

    @abstractmethod
    def summarize_email(self, message: EmailMessage) -> AIResult:
        """Return a one-line summary. Never raises."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(model={self.model!r})"


__all__ = ("AIProvider", "AIResult", "ProviderConfig")
