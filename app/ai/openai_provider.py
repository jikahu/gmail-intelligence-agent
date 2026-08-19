"""OpenAI provider.

The only module in the app that imports the ``openai`` SDK, mirroring
:mod:`app.ai.anthropic_provider`. Same lazy import, same contract, same
never-raise behaviour — the two are interchangeable from the caller's side,
which is the whole point of the provider layer (CLAUDE.md §17).

Anthropic is the default. This exists so the choice stays the user's, set from
the workbook's `ai_provider` setting rather than baked into the code.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai import prompts
from app.ai.base import AIProvider, AIResult, ProviderConfig
from app.ai.costs import AIUsage
from app.ai.schemas import AISuggestion, parse_suggestion, response_json_schema
from app.classification.engine import Classification
from app.classification.message import EmailMessage
from app.logging_config import get_logger

log = get_logger("app.ai.openai")

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(AIProvider):
    """Classification via the OpenAI chat completions API."""

    name = "openai"

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
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_client(self) -> Any:
        if self._client is None:
            import openai

            self._client = openai.OpenAI(
                api_key=self.config.api_key,
                timeout=self.config.timeout_seconds,
                max_retries=2,
            )
        return self._client

    # -------- Requests --------

    def _call(self, user_prompt: str, structured: bool) -> tuple[Any | None, AIResult | None]:
        import openai

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_completion_tokens": self.config.max_output_tokens,
            "messages": [
                {"role": "system", "content": prompts.SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": user_prompt},
            ],
        }
        if structured:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "email_classification",
                    "strict": True,
                    "schema": response_json_schema(),
                },
            }

        try:
            response = self._get_client().chat.completions.create(**kwargs)
        except openai.AuthenticationError:
            return None, AIResult.failed(
                self.name, self.model, "the OpenAI API key was rejected"
            )
        except openai.NotFoundError:
            return None, AIResult.failed(
                self.name, self.model, f"model {self.model!r} was not found"
            )
        except openai.RateLimitError:
            return None, AIResult.failed(
                self.name, self.model, "rate limited by OpenAI; try again shortly"
            )
        except openai.BadRequestError as exc:
            return None, AIResult.failed(self.name, self.model, f"bad request: {exc}")
        except openai.APIConnectionError:
            return None, AIResult.failed(self.name, self.model, "could not reach OpenAI")
        except openai.APIStatusError as exc:
            return None, AIResult.failed(
                self.name, self.model, f"OpenAI error {exc.status_code}"
            )
        except Exception as exc:  # noqa: BLE001 — never break the run
            log.warning("openai_call_failed", extra={"error": str(exc)})
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
                "no OpenAI API key is configured"
                if not self.config.api_key
                else "the `openai` package is not installed",
            )

        response, failure = self._call(
            prompts.build_user_prompt(message, deterministic), structured=True
        )
        if failure is not None:
            return failure

        usage = self._usage_from(response, "classification")
        choice = _first_choice(response)

        if _finish_reason(choice) == "content_filter":
            return AIResult(
                usage=usage, refused=True, prompt_version=prompts.PROMPT_VERSION
            )
        if _finish_reason(choice) == "length":
            return AIResult(
                usage=usage,
                error="the response was cut off before it finished",
                prompt_version=prompts.PROMPT_VERSION,
            )

        payload = _json_from(choice)
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
                self.name, self.model, "no OpenAI API key is configured"
            )

        response, failure = self._call(
            prompts.build_summary_prompt(message), structured=False
        )
        if failure is not None:
            return failure

        usage = self._usage_from(response, "summary")
        choice = _first_choice(response)
        if _finish_reason(choice) == "content_filter":
            return AIResult(usage=usage, refused=True)

        return AIResult(
            usage=usage,
            suggestion=AISuggestion(summary=_content_of(choice)),
            prompt_version=prompts.PROMPT_VERSION,
        )

    def _usage_from(self, response: Any, classification_type: str) -> AIUsage:
        raw = getattr(response, "usage", None)
        return AIUsage.priced(
            provider=self.name,
            model=getattr(response, "model", self.model) or self.model,
            input_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
            classification_type=classification_type,
            prompt_version=prompts.PROMPT_VERSION,
        )


def _first_choice(response: Any) -> Any | None:
    choices = getattr(response, "choices", None) or []
    return choices[0] if choices else None


def _finish_reason(choice: Any | None) -> str:
    return str(getattr(choice, "finish_reason", "") or "")


def _content_of(choice: Any | None) -> str:
    message = getattr(choice, "message", None)
    return str(getattr(message, "content", "") or "").strip()


def _json_from(choice: Any | None) -> dict[str, Any] | None:
    text = _content_of(choice)
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


__all__ = ("DEFAULT_MODEL", "OpenAIProvider")
