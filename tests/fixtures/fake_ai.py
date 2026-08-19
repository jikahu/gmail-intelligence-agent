"""Fake AI providers. No network, no API key, no spend (CLAUDE.md §17)."""

from __future__ import annotations

from typing import Any

from app.ai.base import AIProvider, AIResult, ProviderConfig
from app.ai.costs import AIUsage
from app.ai.schemas import AISuggestion, parse_suggestion
from app.classification.engine import Classification
from app.classification.message import EmailMessage

#: A well-formed, unremarkable answer.
PLAIN_ANSWER: dict[str, Any] = {
    "labels": ["AI/Work-Business"],
    "priority": "P3",
    "confidence": 0.85,
    "summary": "A routine notice from a vendor.",
    "action_required": False,
    "rationale": "Reads like ordinary business correspondence.",
}


class FakeProvider(AIProvider):
    """Returns whatever payload it was given, and records what it was asked."""

    name = "fake"

    def __init__(
        self,
        payload: dict[str, Any] | AISuggestion | None = None,
        model: str = "claude-opus-5",
        input_tokens: int = 1200,
        output_tokens: int = 180,
        refused: bool = False,
        error: str | None = None,
        configured: bool = True,
    ) -> None:
        super().__init__(ProviderConfig(model=model, api_key="test-key"))
        self.payload = payload if payload is not None else PLAIN_ANSWER
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.refused = refused
        self.error = error
        self._configured = configured
        #: Every message the provider was asked about, in order.
        self.calls: list[EmailMessage] = []
        #: The prompts it was given, for injection-defense assertions.
        self.prompts: list[str] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    def _usage(self) -> AIUsage:
        return AIUsage.priced(
            provider=self.name,
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )

    def classify_email(
        self, message: EmailMessage, deterministic: Classification | None = None
    ) -> AIResult:
        from app.ai import prompts as prompt_module

        self.calls.append(message)
        self.prompts.append(prompt_module.build_user_prompt(message, deterministic))

        if not self._configured:
            return AIResult.skipped(self.name, self.model, "not configured")
        if self.error:
            return AIResult.failed(self.name, self.model, self.error)
        if self.refused:
            return AIResult(usage=self._usage(), refused=True)

        return AIResult(
            usage=self._usage(),
            suggestion=parse_suggestion(self.payload),
            prompt_version=prompt_module.PROMPT_VERSION,
        )

    def summarize_email(self, message: EmailMessage) -> AIResult:
        return AIResult(
            usage=self._usage(),
            suggestion=AISuggestion(summary="A one-line summary."),
        )


class ExplodingProvider(AIProvider):
    """Raises on every call — proves failures never break classification."""

    name = "exploding"

    def __init__(self) -> None:
        super().__init__(ProviderConfig(model="boom-1", api_key="k"))

    @property
    def is_configured(self) -> bool:
        return True

    def classify_email(
        self, message: EmailMessage, deterministic: Classification | None = None
    ) -> AIResult:
        raise RuntimeError("provider exploded")

    def summarize_email(self, message: EmailMessage) -> AIResult:
        raise RuntimeError("provider exploded")


__all__ = ("ExplodingProvider", "FakeProvider", "PLAIN_ANSWER")
