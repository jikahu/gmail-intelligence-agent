"""Structured output contract for AI classification (CLAUDE.md §11).

The AI is never trusted to return well-formed anything. Every field here is
validated by Pydantic, and anything outside the contract is dropped rather
than passed along:

* Labels that aren't in the taxonomy are discarded, not invented.
* Confidence is clamped to 0.0–1.0.
* Free text is length-capped so a hostile or broken response can't flood the
  dashboard, the audit log, or a later prompt.

Passing validation only means the *shape* is acceptable. Whether the content
is allowed to affect the outcome is decided separately by
:mod:`app.ai.validator`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.classification.labels import Label, Priority

#: Caps on free-text fields the AI returns. Generous enough to be useful,
#: small enough that a runaway response can't fill a spreadsheet cell.
MAX_SUMMARY_CHARS = 300
MAX_REASON_CHARS = 300
MAX_RATIONALE_CHARS = 400
MAX_INDICATORS = 10
MAX_LABELS = 8

_VALID_LABEL_VALUES: frozenset[str] = frozenset(label.value for label in Label)
_LABELS_BY_LOWER: dict[str, Label] = {label.value.lower(): label for label in Label}


def _clean_text(value: str | None, limit: int) -> str:
    """Collapse whitespace, strip control characters, and truncate."""
    if not value:
        return ""
    text = " ".join(str(value).split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:limit]


class AISuggestion(BaseModel):
    """What the AI proposes. A recommendation — never an instruction.

    Deliberately absent: any field that could move mail on its own. There is
    no ``archive``, no ``trash``, no ``delete``, no ``apply_labels``. The AI
    has no vocabulary for acting, only for suggesting (CLAUDE.md §16).
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    labels: list[Label] = Field(default_factory=list)
    priority: Priority = Priority.P3_NORMAL
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    review_reason: str | None = None
    action_required: bool = False
    #: ISO date if the AI spotted a deadline. Extraction proper is Phase 6.
    deadline: str | None = None
    amount: float | None = None
    currency: str | None = None
    suspicion_indicators: list[str] = Field(default_factory=list)
    #: Short user-facing decision rationale. NEVER hidden chain-of-thought.
    rationale: str = ""

    # -------- Validators --------

    @field_validator("labels", mode="before")
    @classmethod
    def _coerce_labels(cls, value: Any) -> list[Label]:
        """Keep only real taxonomy labels; silently drop anything invented."""
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            return []

        kept: list[Label] = []
        for item in value:
            if isinstance(item, Label):
                kept.append(item)
                continue
            name = str(item).strip()
            match = _LABELS_BY_LOWER.get(name.lower())
            if match is None and not name.lower().startswith("ai/"):
                match = _LABELS_BY_LOWER.get(f"ai/{name.lower()}")
            if match is not None and match not in kept:
                kept.append(match)
        return kept[:MAX_LABELS]

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, value: Any) -> Priority:
        if isinstance(value, Priority):
            return value
        raw = str(value or "").strip().upper()
        for priority in Priority:
            if raw == priority.value or raw.startswith(priority.value):
                return priority
        return Priority.P3_NORMAL

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: Any) -> float:
        """Normalize confidence, treating anything unclear as *unknown*.

        Rules, in order:

        * ``0.0``–``1.0`` — used as given.
        * ``2``–``100`` — read as a percentage (some models answer "85").
        * Anything else, including ``1.5``, ``-3``, ``NaN`` and non-numbers —
          becomes ``0.0``.

        The last rule is the important one. An out-of-range number means we
        don't actually know how sure the model was, and 0.0 is the safe
        reading: low confidence routes to Review (subject to the protection
        veto), whereas guessing high would give a garbled answer more weight
        than it earned.
        """
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number != number:  # NaN
            return 0.0
        if 0.0 <= number <= 1.0:
            return number
        if 2.0 <= number <= 100.0:
            return number / 100.0
        return 0.0

    @field_validator("summary", mode="before")
    @classmethod
    def _clean_summary(cls, value: Any) -> str:
        return _clean_text(value, MAX_SUMMARY_CHARS)

    @field_validator("review_reason", mode="before")
    @classmethod
    def _clean_reason(cls, value: Any) -> str | None:
        cleaned = _clean_text(value, MAX_REASON_CHARS)
        return cleaned or None

    @field_validator("rationale", mode="before")
    @classmethod
    def _clean_rationale(cls, value: Any) -> str:
        return _clean_text(value, MAX_RATIONALE_CHARS)

    @field_validator("deadline", mode="before")
    @classmethod
    def _clean_deadline(cls, value: Any) -> str | None:
        cleaned = _clean_text(value, 40)
        return cleaned or None

    @field_validator("currency", mode="before")
    @classmethod
    def _clean_currency(cls, value: Any) -> str | None:
        cleaned = _clean_text(value, 8).upper()
        return cleaned or None

    @field_validator("amount", mode="before")
    @classmethod
    def _clean_amount(cls, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            number = float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return None
        return number if number == number else None

    @field_validator("suspicion_indicators", mode="before")
    @classmethod
    def _clean_indicators(cls, value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            return []
        cleaned = [_clean_text(item, 120) for item in value]
        return [item for item in cleaned if item][:MAX_INDICATORS]

    # -------- Views --------

    @property
    def label_set(self) -> set[Label]:
        return set(self.labels)

    @property
    def is_suspicious(self) -> bool:
        return Label.SUSPICIOUS in self.labels or bool(self.suspicion_indicators)

    def safe_rationale(self) -> str:
        """The one-line explanation shown to the user."""
        return self.rationale or self.summary or "The AI did not explain its answer."


#: JSON Schema handed to providers that support constrained output.
#: Built from the model so the two can never drift apart.
def response_json_schema() -> dict[str, Any]:
    """Return a strict JSON Schema for :class:`AISuggestion`."""
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(_VALID_LABEL_VALUES)},
            },
            "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
            "confidence": {"type": "number"},
            "summary": {"type": "string"},
            "review_reason": {"type": ["string", "null"]},
            "action_required": {"type": "boolean"},
            "deadline": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]},
            "suspicion_indicators": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "required": [
            "labels",
            "priority",
            "confidence",
            "summary",
            "action_required",
            "rationale",
        ],
        "additionalProperties": False,
    }


def parse_suggestion(payload: Any) -> AISuggestion:
    """Build a suggestion from whatever the provider returned.

    Never raises on bad content — an unusable response becomes an empty,
    zero-confidence suggestion, which the validator then ignores.
    """
    if isinstance(payload, AISuggestion):
        return payload
    if not isinstance(payload, dict):
        return AISuggestion()
    try:
        return AISuggestion.model_validate(payload)
    except Exception:  # noqa: BLE001 — a malformed answer is not a crash
        return AISuggestion()


__all__ = (
    "AISuggestion",
    "MAX_LABELS",
    "MAX_RATIONALE_CHARS",
    "MAX_REASON_CHARS",
    "MAX_SUMMARY_CHARS",
    "parse_suggestion",
    "response_json_schema",
)
