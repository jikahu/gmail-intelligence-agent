"""Provider independence and cost accounting (CLAUDE.md §17).

No test here makes a network call or needs an API key.
"""

from __future__ import annotations

import pytest

from app.ai.base import AIProvider, AIResult, ProviderConfig
from app.ai.costs import MODEL_PRICES, AIUsage, CostTracker, estimate_cost, price_for
from app.ai.factory import (
    KNOWN_PROVIDERS,
    NullProvider,
    build_provider,
    describe_provider,
)
from tests.fixtures.emails import make_message
from tests.fixtures.fake_ai import FakeProvider


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()


# --------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------


def test_anthropic_is_the_default() -> None:
    assert build_provider().name in {"anthropic", "none"}
    assert build_provider(provider_name="anthropic").name == "anthropic"


def test_provider_can_be_swapped_by_name() -> None:
    assert build_provider(provider_name="openai").name == "openai"
    assert build_provider(provider_name="anthropic").name == "anthropic"


def test_provider_can_be_swapped_from_the_workbook() -> None:
    provider = build_provider(workbook_settings={"ai_provider": "openai"})
    assert provider.name == "openai"


def test_explicit_argument_beats_the_workbook() -> None:
    provider = build_provider(
        provider_name="anthropic", workbook_settings={"ai_provider": "openai"}
    )
    assert provider.name == "anthropic"


def test_model_comes_from_the_workbook_when_set() -> None:
    provider = build_provider(
        provider_name="anthropic",
        workbook_settings={"anthropic_model": "claude-haiku-4-5"},
    )
    assert provider.model == "claude-haiku-4-5"


def test_unknown_provider_falls_back_to_null_not_a_crash() -> None:
    provider = build_provider(provider_name="definitely-not-a-provider")

    assert isinstance(provider, NullProvider)
    assert not provider.is_configured


def test_known_providers_are_the_two_the_spec_requires() -> None:
    assert KNOWN_PROVIDERS == {"anthropic", "openai"}


def test_describe_provider_never_leaks_the_key() -> None:
    provider = build_provider(provider_name="anthropic", api_key="sk-secret-value")
    described = describe_provider(provider)

    assert "sk-secret-value" not in str(described)
    assert set(described) == {"provider", "model", "configured"}


# --------------------------------------------------------------------
# Behaviour without credentials
# --------------------------------------------------------------------


def test_an_unconfigured_provider_skips_rather_than_failing() -> None:
    result = build_provider(provider_name="anthropic").classify_email(make_message())

    assert not result.was_called
    assert result.skipped_reason
    assert result.suggestion is None


def test_null_provider_always_skips() -> None:
    provider = NullProvider("no key")
    message = make_message()

    assert not provider.classify_email(message).was_called
    assert not provider.summarize_email(message).was_called


def test_attachment_analysis_is_stubbed_until_phase_5() -> None:
    result = NullProvider().analyze_attachment("x.pdf", "some text")
    assert "Phase 5" in (result.skipped_reason or "")


# --------------------------------------------------------------------
# Provider contract
# --------------------------------------------------------------------


def test_providers_share_one_interface() -> None:
    from app.ai.anthropic_provider import AnthropicProvider
    from app.ai.openai_provider import OpenAIProvider

    for cls in (AnthropicProvider, OpenAIProvider, NullProvider, FakeProvider):
        assert issubclass(cls, AIProvider)
        for method in ("classify_email", "summarize_email", "analyze_attachment"):
            assert callable(getattr(cls, method))


def test_no_vendor_sdk_is_imported_outside_its_own_provider_module() -> None:
    """CLAUDE.md §3: no vendor SDK calls scattered through the codebase."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[2] / "app"
    allowed = {"anthropic_provider.py", "openai_provider.py"}

    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import anthropic", "from anthropic")):
                offenders.append(f"{path.name}: {stripped}")
            if stripped.startswith(("import openai", "from openai")):
                offenders.append(f"{path.name}: {stripped}")

    assert not offenders, offenders


def test_result_describe_is_human_readable() -> None:
    assert "not consulted" in AIResult.skipped("p", "m", "no key").describe()
    assert "unavailable" in AIResult.failed("p", "m", "timeout").describe()
    assert "declined" in AIResult(
        usage=AIUsage(provider="p", model="m"), refused=True
    ).describe()


# --------------------------------------------------------------------
# Cost accounting
# --------------------------------------------------------------------


def test_known_models_are_priced() -> None:
    for model in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
        assert price_for(model) is not None, model


def test_cost_is_computed_per_million_tokens() -> None:
    # claude-opus-5 is $5 in / $25 out per million.
    cost = estimate_cost("claude-opus-5", input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(5.00)

    cost = estimate_cost("claude-opus-5", input_tokens=0, output_tokens=1_000_000)
    assert cost == pytest.approx(25.00)

    cost = estimate_cost("claude-opus-5", input_tokens=1_000, output_tokens=200)
    assert cost == pytest.approx(0.005 + 0.005)


def test_a_cheaper_model_costs_less() -> None:
    opus = estimate_cost("claude-opus-5", 100_000, 10_000)
    haiku = estimate_cost("claude-haiku-4-5", 100_000, 10_000)

    assert haiku < opus


def test_unknown_model_costs_zero_rather_than_crashing() -> None:
    assert estimate_cost("some-future-model", 1000, 1000) == 0.0


def test_negative_token_counts_do_not_produce_negative_cost() -> None:
    assert estimate_cost("claude-opus-5", -5, -5) == 0.0


def test_tracker_accumulates() -> None:
    tracker = CostTracker()
    tracker.record(AIUsage.priced("anthropic", "claude-opus-5", 1000, 200))
    tracker.record(AIUsage.priced("anthropic", "claude-opus-5", 2000, 400))

    assert tracker.call_count == 2
    assert tracker.total_tokens == 3600
    assert tracker.total_cost_usd > 0
    assert tracker.summary()["ai_calls"] == 2


def test_tracker_counts_avoidable_calls() -> None:
    """The number that says whether the rules engine is pulling its weight."""
    tracker = CostTracker()
    tracker.record(AIUsage.priced("anthropic", "claude-opus-5", 100, 10))
    tracker.record(
        AIUsage.priced(
            "anthropic", "claude-opus-5", 100, 10, could_have_used_rule=True
        )
    )

    assert tracker.avoidable_calls == 1
    assert tracker.summary()["avoidable_calls"] == 1


def test_tracker_groups_by_model() -> None:
    tracker = CostTracker()
    tracker.record(AIUsage.priced("anthropic", "claude-opus-5", 10, 1))
    tracker.record(AIUsage.priced("anthropic", "claude-haiku-4-5", 10, 1))
    tracker.record(AIUsage.priced("anthropic", "claude-haiku-4-5", 10, 1))

    assert tracker.summary()["calls_by_model"] == {
        "claude-opus-5": 1,
        "claude-haiku-4-5": 2,
    }


def test_price_table_covers_the_configured_defaults() -> None:
    from app.ai.anthropic_provider import DEFAULT_MODEL as ANTHROPIC_DEFAULT
    from app.ai.openai_provider import DEFAULT_MODEL as OPENAI_DEFAULT

    assert ANTHROPIC_DEFAULT in MODEL_PRICES
    assert OPENAI_DEFAULT in MODEL_PRICES
