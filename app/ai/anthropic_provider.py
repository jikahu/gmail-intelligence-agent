"""Anthropic provider.

The only module in the app that imports the ``anthropic`` SDK. The import is
deliberately lazy so the app boots, and the whole test suite runs, without the
package installed — an AI provider that isn't set up degrades classification to
deterministic-only rather than breaking it.

Requests use constrained JSON output so the response is a schema-checked object
rather than prose we have to scrape. Adaptive thinking is left on (the default
on current models) with a low effort setting: this is a short classification
task, and low effort keeps it cheap without hurting quality.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai import prompts
from app.ai.base import AIProvider, AIResult, ProviderConfig
from app.ai.costs import AIUsage
from app.ai.schemas import parse_suggestion, response_json_schema
from app.classification.engine import Classification
from app.classification.message import EmailMessage
from app.logging_config import get_logger

log = get_logger("app.ai.anthropic")

#: Default model. Chosen as the most capable current model; switch it in the
#: workbook Settings tab (`anthropic_model`) if you want a cheaper one.
DEFAULT_MODEL = "claude-opus-5"


class AnthropicProvider(AIProvider):
    """Classification via the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: Any | None = None

    # -------- Client --------

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key) and self._sdk_available()

    @staticmethod
    def _sdk_available() -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=self.config.api_key,
                timeout=self.config.timeout_seconds,
                max_retries=2,
            )
        return self._client

    # -------- Requests --------

    def _request_kwargs(self, structured: bool) -> dict[str, Any]:
        output_config: dict[str, Any] = {}
        if self.config.effort:
            output_config["effort"] = self.config.effort
        if structured:
            output_config["format"] = {
                "type": "json_schema",
                "schema": response_json_schema(),
            }

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": prompts.SYSTEM_INSTRUCTIONS,
        }
        if output_config:
            kwargs["output_config"] = output_config
        return kwargs

    def _call(self, user_prompt: str, structured: bool) -> tuple[Any | None, AIResult | None]:
        """Make one request. Returns ``(response, error_result)`` — one is None."""
        import anthropic

        try:
            response = self._get_client().messages.create(
                messages=[{"role": "user", "content": user_prompt}],
                **self._request_kwargs(structured),
            )
        except anthropic.AuthenticationError:
            return None, AIResult.failed(
                self.name, self.model, "the Anthropic API key was rejected"
            )
        except anthropic.NotFoundError:
            return None, AIResult.failed(
                self.name, self.model, f"model {self.model!r} was not found"
            )
        except anthropic.RateLimitError:
            return None, AIResult.failed(
                self.name, self.model, "rate limited by Anthropic; try again shortly"
            )
        except anthropic.BadRequestError as exc:
            return None, AIResult.failed(self.name, self.model, f"bad request: {exc}")
        except anthropic.APIConnectionError:
            return None, AIResult.failed(
                self.name, self.model, "could not reach Anthropic"
            )
        except anthropic.APIStatusError as exc:
            return None, AIResult.failed(
                self.name, self.model, f"Anthropic error {exc.status_code}"
            )
        except Exception as exc:  # noqa: BLE001 — never break the run
            log.warning("anthropic_call_failed", extra={"error": str(exc)})
            return None, AIResult.failed(self.name, self.model, str(exc))

        return response, None

    # -------- Interface --------

    def classify_email(
        self,
        message: EmailMessage,
        deterministic: Classification | None = None,
    ) -> AIResult:
        if not self.is_configured:
            return AIResult.skipped(
                self.name,
                self.model,
                "no Anthropic API key is configured"
                if not self.config.api_key
                else "the `anthropic` package is not installed",
            )

        response, failure = self._call(
            prompts.build_user_prompt(message, deterministic), structured=True
        )
        if failure is not None:
            return failure

        usage = self._usage_from(response, "classification")

        if getattr(response, "stop_reason", None) == "refusal":
            return AIResult(
                usage=usage, refused=True, prompt_version=prompts.PROMPT_VERSION
            )
        if getattr(response, "stop_reason", None) == "max_tokens":
            return AIResult(
                usage=usage,
                error="the response was cut off before it finished",
                prompt_version=prompts.PROMPT_VERSION,
            )

        payload = _first_json_object(response)
        if payload is None:
            return AIResult(
                usage=usage,
                error="the response was not valid JSON",
                prompt_version=prompts.PROMPT_VERSION,
            )

        return AIResult(
            usage=usage,
            suggestion=parse_suggestion(payload),
            prompt_version=prompts.PROMPT_VERSION,
        )

    def summarize_email(self, message: EmailMessage) -> AIResult:
        if not self.is_configured:
            return AIResult.skipped(
                self.name, self.model, "no Anthropic API key is configured"
            )

        response, failure = self._call(
            prompts.build_summary_prompt(message), structured=False
        )
        if failure is not None:
            return failure

        usage = self._usage_from(response, "summary")
        if getattr(response, "stop_reason", None) == "refusal":
            return AIResult(usage=usage, refused=True)

        from app.ai.schemas import AISuggestion

        return AIResult(
            usage=usage,
            suggestion=AISuggestion(summary=_first_text(response)),
            prompt_version=prompts.PROMPT_VERSION,
        )

    def _usage_from(self, response: Any, classification_type: str) -> AIUsage:
        raw = getattr(response, "usage", None)
        return AIUsage.priced(
            provider=self.name,
            model=getattr(response, "model", self.model) or self.model,
            input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
            output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
            classification_type=classification_type,
            prompt_version=prompts.PROMPT_VERSION,
        )


def _first_text(response: Any) -> str:
    """Concatenate the text blocks of a Messages API response."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return " ".join(part for part in parts if part).strip()


def _first_json_object(response: Any) -> dict[str, Any] | None:
    """Parse the response text as JSON, tolerating stray prose around it."""
    text = _first_text(response)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ("DEFAULT_MODEL", "AnthropicProvider")
