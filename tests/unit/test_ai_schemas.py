"""Structured-output validation — nothing the AI returns is trusted as-is."""

from __future__ import annotations

import pytest

from app.ai.schemas import (
    MAX_LABELS,
    MAX_RATIONALE_CHARS,
    MAX_SUMMARY_CHARS,
    AISuggestion,
    parse_suggestion,
    response_json_schema,
)
from app.classification.labels import Label, Priority


# --------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------


def test_valid_labels_are_kept() -> None:
    suggestion = parse_suggestion({"labels": ["AI/Financial", "AI/Action-Required"]})
    assert set(suggestion.labels) == {Label.FINANCIAL, Label.ACTION_REQUIRED}


def test_invented_labels_are_dropped_not_created() -> None:
    suggestion = parse_suggestion(
        {"labels": ["AI/Financial", "AI/Definitely-Spam", "DELETE_EVERYTHING"]}
    )
    assert suggestion.labels == [Label.FINANCIAL]


def test_label_matching_is_case_insensitive_and_tolerates_a_missing_prefix() -> None:
    suggestion = parse_suggestion({"labels": ["ai/financial", "Personal"]})
    assert set(suggestion.labels) == {Label.FINANCIAL, Label.PERSONAL}


def test_a_bare_string_label_is_accepted() -> None:
    assert parse_suggestion({"labels": "AI/Security"}).labels == [Label.SECURITY]


def test_label_count_is_capped() -> None:
    suggestion = parse_suggestion({"labels": [label.value for label in Label]})
    assert len(suggestion.labels) <= MAX_LABELS


def test_duplicate_labels_collapse() -> None:
    suggestion = parse_suggestion({"labels": ["AI/Financial", "AI/Financial"]})
    assert suggestion.labels == [Label.FINANCIAL]


# --------------------------------------------------------------------
# Priority and confidence
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("P1", Priority.P1_URGENT),
        ("p2", Priority.P2_IMPORTANT),
        ("P3 Normal", Priority.P3_NORMAL),
        ("urgent", Priority.P3_NORMAL),
        ("", Priority.P3_NORMAL),
        (None, Priority.P3_NORMAL),
    ],
)
def test_priority_parsing_falls_back_safely(raw, expected) -> None:
    assert parse_suggestion({"priority": raw}).priority is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.85, 0.85),
        (1.0, 1.0),
        (85, 0.85),        # a 0-100 scale is read as a percentage
        (100, 1.0),
        (1.5, 0.0),        # out of range → unknown, not "very confident"
        (150, 0.0),
        (-3, 0.0),
        ("0.4", 0.4),
        ("nonsense", 0.0),
        (None, 0.0),
        (float("nan"), 0.0),
    ],
)
def test_out_of_range_confidence_becomes_unknown(raw, expected) -> None:
    """A garbled number must never read as high confidence."""
    assert parse_suggestion({"confidence": raw}).confidence == pytest.approx(expected)


# --------------------------------------------------------------------
# Free text
# --------------------------------------------------------------------


def test_long_text_is_truncated() -> None:
    suggestion = parse_suggestion(
        {"summary": "x" * 5000, "rationale": "y" * 5000}
    )
    assert len(suggestion.summary) <= MAX_SUMMARY_CHARS
    assert len(suggestion.rationale) <= MAX_RATIONALE_CHARS


def test_control_characters_are_stripped() -> None:
    suggestion = parse_suggestion({"summary": "hello\x00\x07 world\n\tthere"})
    assert "\x00" not in suggestion.summary
    assert suggestion.summary == "hello world there"


def test_empty_review_reason_becomes_none() -> None:
    assert parse_suggestion({"review_reason": "   "}).review_reason is None


def test_suspicion_indicators_are_cleaned_and_capped() -> None:
    suggestion = parse_suggestion(
        {"suspicion_indicators": ["a" * 500] + [f"item {i}" for i in range(50)]}
    )
    assert len(suggestion.suspicion_indicators) <= 10
    assert all(len(item) <= 120 for item in suggestion.suspicion_indicators)


def test_amount_parsing_tolerates_formatting() -> None:
    assert parse_suggestion({"amount": "$1,234.50"}).amount == pytest.approx(1234.50)
    assert parse_suggestion({"amount": "not a number"}).amount is None
    assert parse_suggestion({"amount": None}).amount is None


# --------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------


def test_garbage_payloads_produce_an_empty_suggestion_not_a_crash() -> None:
    for payload in (None, "a string", 42, [], {"labels": {"nested": "dict"}}):
        suggestion = parse_suggestion(payload)
        assert isinstance(suggestion, AISuggestion)
        assert suggestion.confidence == 0.0


def test_unknown_fields_are_ignored() -> None:
    suggestion = parse_suggestion(
        {"labels": ["AI/Personal"], "execute_shell": "rm -rf /", "trash": True}
    )
    assert suggestion.labels == [Label.PERSONAL]
    assert not hasattr(suggestion, "execute_shell")
    assert not hasattr(suggestion, "trash")


def test_the_schema_has_no_vocabulary_for_acting() -> None:
    """The AI cannot ask for a Gmail action because no such field exists."""
    fields = set(AISuggestion.model_fields)
    for verb in ("archive", "trash", "delete", "send", "apply", "move", "execute"):
        assert not any(verb in field for field in fields), verb


def test_json_schema_matches_the_model() -> None:
    schema = response_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) <= set(AISuggestion.model_fields)
    label_enum = set(schema["properties"]["labels"]["items"]["enum"])
    assert label_enum == {label.value for label in Label}
