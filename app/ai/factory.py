"""Provider selection (CLAUDE.md §17 — provider independence).

Which AI runs is a setting, not a code path. The resolution order is:

1. An explicit argument (tests, one-off runs).
2. The workbook ``Settings`` tab — the user's own control panel.
3. ``.env`` / environment variables.
4. Anthropic with its default model.

If nothing is configured, :class:`NullProvider` stands in and reports that AI
was skipped. Callers never have to check whether AI is available.
"""

from __future__ import annotations

from app.ai.base import AIProvider, AIResult, ProviderConfig
from app.ai.schemas import AISuggestion  # noqa: F401 — re-exported for callers
from app.classification.engine import Classification
from app.classification.message import EmailMessage
from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.ai.factory")

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"

KNOWN_PROVIDERS: frozenset[str] = frozenset({PROVIDER_ANTHROPIC, PROVIDER_OPENAI})


class NullProvider(AIProvider):
    """Stands in when no AI is configured. Always skips, never fails."""

    name = "none"

    def __init__(self, reason: str = "no AI provider is configured") -> None:
        super().__init__(ProviderConfig(model="none"))
        self.reason = reason

    @property
    def is_configured(self) -> bool:
        return False

    def classify_email(
        self, message: EmailMessage, deterministic: Classification | None = None
    ) -> AIResult:
        return AIResult.skipped(self.name, self.model, self.reason)

    def summarize_email(self, message: EmailMessage) -> AIResult:
        return AIResult.skipped(self.name, self.model, self.reason)


def build_provider(
    provider_name: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    workbook_settings: dict[str, str] | None = None,
    effort: str | None = None,
) -> AIProvider:
    """Construct the configured provider, or a :class:`NullProvider`."""
    settings = get_settings()
    workbook_settings = workbook_settings or {}

    resolved_name = (
        provider_name
        or workbook_settings.get("ai_provider")
        or settings.ai_provider
        or PROVIDER_ANTHROPIC
    ).strip().lower()

    if resolved_name not in KNOWN_PROVIDERS:
        log.warning("unknown_ai_provider", extra={"requested": resolved_name})
        return NullProvider(f"{resolved_name!r} is not a provider this app knows about")

    if resolved_name == PROVIDER_ANTHROPIC:
        from app.ai.anthropic_provider import DEFAULT_MODEL, AnthropicProvider

        config = ProviderConfig(
            model=model or workbook_settings.get("anthropic_model") or settings.anthropic_model or DEFAULT_MODEL,
            api_key=api_key or settings.anthropic_api_key,
            effort=effort or workbook_settings.get("ai_effort") or "low",
        )
        provider: AIProvider = AnthropicProvider(config)
    else:
        from app.ai.openai_provider import DEFAULT_MODEL, OpenAIProvider

        config = ProviderConfig(
            model=model or workbook_settings.get("openai_model") or settings.openai_model or DEFAULT_MODEL,
            api_key=api_key or settings.openai_api_key,
            effort=effort or workbook_settings.get("ai_effort") or "low",
        )
        provider = OpenAIProvider(config)

    if not provider.is_configured:
        log.info(
            "ai_provider_not_configured",
            extra={"provider": resolved_name, "ai_model": provider.model},
        )
    return provider


def describe_provider(provider: AIProvider) -> dict[str, object]:
    """A JSON-safe view for the dashboard. Never includes the key."""
    return {
        "provider": provider.name,
        "model": provider.model,
        "configured": provider.is_configured,
    }


__all__ = (
    "KNOWN_PROVIDERS",
    "NullProvider",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OPENAI",
    "build_provider",
    "describe_provider",
)
