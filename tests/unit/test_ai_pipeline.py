"""AI in the live pipeline — when it's consulted, and what reaches the page."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ai.assist import assist, should_consult
from app.ai.costs import CostTracker
from app.classification.context import ClassificationContext
from app.classification.engine import classify
from app.classification.pipeline import preview_recent, summarize
from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, gmail_message
from tests.fixtures.fake_ai import PLAIN_ANSWER, ExplodingProvider, FakeProvider


class FakeGmailClient:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages

    def get_profile(self) -> dict:
        return {"emailAddress": DEFAULT_USER}

    def list_recent_messages(self, max_results: int = 10, query: str | None = None):
        return self._messages[:max_results]


def _messages() -> list[dict]:
    return [
        # Settled by the rules — must not cost an AI call.
        gmail_message(
            message_id="settled",
            headers={
                "From": "Chase <alerts@chase.com>",
                "To": DEFAULT_USER,
                "Subject": "Your account statement is ready",
            },
            plain_body="Your statement is available.",
        ),
        # Also settled — obvious promotion.
        gmail_message(
            message_id="promo",
            headers={
                "From": "Deals <promo@shop.example>",
                "To": DEFAULT_USER,
                "Subject": "FLASH SALE - 50% off",
                **{k.title(): v for k, v in bulk_headers().items()},
            },
            plain_body="Shop now. Unsubscribe here.",
        ),
        # Unresolved — this is the one worth asking about.
        gmail_message(
            message_id="unclear",
            headers={"From": "no-reply@unknown.example", "To": DEFAULT_USER, "Subject": ""},
            plain_body="",
        ),
    ]


@pytest.fixture
def fake_gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmailClient:
    client = FakeGmailClient(_messages())
    monkeypatch.setattr("app.gmail.client.get_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _no_live_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )


# --------------------------------------------------------------------
# The cost gate
# --------------------------------------------------------------------


def test_ai_is_only_consulted_for_unresolved_messages(fake_gmail) -> None:
    """Hard rules first, AI second (CLAUDE.md §3)."""
    provider = FakeProvider()
    tracker = CostTracker()

    preview_recent(limit=10, provider=provider, tracker=tracker)

    assert len(provider.calls) == 1
    assert provider.calls[0].message_id == "unclear"
    assert tracker.call_count == 1


def test_settled_messages_cost_nothing(fake_gmail) -> None:
    context = ClassificationContext(user_email=DEFAULT_USER)
    from app.classification.message import from_gmail

    settled = classify(from_gmail(_messages()[0], DEFAULT_USER), context)

    assert not should_consult(settled)


def test_sent_mail_is_never_sent_to_the_ai() -> None:
    from tests.fixtures.emails import make_message

    decision = classify(
        make_message(sender=DEFAULT_USER, subject="mine", sent_by_user=True),
        ClassificationContext(user_email=DEFAULT_USER),
    )
    assert not should_consult(decision)


def test_ai_can_be_turned_off_entirely(fake_gmail) -> None:
    provider = FakeProvider()

    results = preview_recent(limit=10, use_ai=False, provider=provider)

    assert provider.calls == []
    assert all(r.ai is None for r in results)


# --------------------------------------------------------------------
# Failure isolation
# --------------------------------------------------------------------


def test_a_provider_that_raises_does_not_break_the_run(fake_gmail) -> None:
    from tests.fixtures.emails import make_message

    message = make_message(sender="unknown@x.com", subject="")
    base = classify(message, ClassificationContext())

    with pytest.raises(RuntimeError):
        ExplodingProvider().classify_email(message)

    # But the real providers catch their own errors — a failed call degrades
    # the classification instead of ending the run.
    outcome = assist(message, base, FakeProvider(error="boom"), force=True)
    assert outcome.classification.labels == base.labels


def test_an_unconfigured_provider_leaves_everything_deterministic(fake_gmail) -> None:
    results = preview_recent(
        limit=10, provider=FakeProvider(configured=False), tracker=CostTracker()
    )

    assert len(results) == 3
    assert all(r.classification is not None for r in results)


# --------------------------------------------------------------------
# What reaches the page
# --------------------------------------------------------------------


def test_preview_view_reports_the_ai_step(fake_gmail) -> None:
    results = preview_recent(limit=10, provider=FakeProvider())
    unclear = next(r for r in results if r.message.message_id == "unclear")

    view = unclear.as_dict()["ai"]

    assert view["consulted"] is True
    assert view["provider"] == "fake"
    assert view["confidence"] == pytest.approx(PLAIN_ANSWER["confidence"])
    assert view["prompt_version"]
    assert "estimated_cost_usd" in view


def test_preview_view_omits_ai_for_messages_it_never_saw(fake_gmail) -> None:
    results = preview_recent(limit=10, provider=FakeProvider())
    settled = next(r for r in results if r.message.message_id == "settled")

    assert "ai" not in settled.as_dict()


def test_ai_view_never_contains_the_email_body(fake_gmail) -> None:
    results = preview_recent(limit=10, provider=FakeProvider())

    for result in results:
        assert "Your statement is available" not in str(result.as_dict())


def test_summary_counts_ai_consultations(fake_gmail) -> None:
    summary = summarize(preview_recent(limit=10, provider=FakeProvider()))

    assert summary["total"] == 3
    assert summary["ai_consulted"] == 1
    assert summary["protected_routed_to_review"] == 0


# --------------------------------------------------------------------
# Route
# --------------------------------------------------------------------


def test_preview_route_reports_provider_and_cost(client: TestClient, fake_gmail) -> None:
    save_token(StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES)))

    body = client.get("/classify/preview?limit=10").json()

    assert body["gmail_modified"] is False
    assert "ai" in body
    assert set(body["ai"]) == {"provider", "model", "configured"}
    assert "cost" in body
    assert body["cost"]["ai_calls"] >= 0


def test_preview_route_can_disable_ai(client: TestClient, fake_gmail) -> None:
    save_token(StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES)))

    body = client.get("/classify/preview?limit=5&ai=false").json()

    assert body["ai"] == {"enabled": False}
    assert body["cost"]["ai_calls"] == 0


def test_preview_route_never_exposes_an_api_key(
    client: TestClient, fake_gmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-do-not-leak")
    from app.config import get_settings

    get_settings.cache_clear()
    save_token(StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES)))

    assert "sk-ant-do-not-leak" not in client.get("/classify/preview?limit=3").text
