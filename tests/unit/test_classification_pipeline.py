"""The live pipeline and the read-only /classify/preview route."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.classification.engine import Classification, classify
from app.classification.pipeline import (
    MAX_PREVIEW_MESSAGES,
    PreviewResult,
    build_live_context,
    preview_recent,
    summarize,
)
from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, gmail_message, make_message


class FakeGmailClient:
    """Stands in for GmailReadClient — read methods only."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self.requested_limits: list[int] = []

    def get_profile(self) -> dict:
        return {"emailAddress": DEFAULT_USER}

    def list_recent_messages(self, max_results: int = 10, query: str | None = None):
        self.requested_limits.append(max_results)
        return self._messages[:max_results]


def _sample_messages() -> list[dict]:
    return [
        gmail_message(
            message_id="m1",
            headers={
                "From": "Chase <alerts@chase.com>",
                "To": DEFAULT_USER,
                "Subject": "Your account statement is ready",
            },
            plain_body="Your August statement is available to view.",
        ),
        gmail_message(
            message_id="m2",
            headers={
                "From": "Deals <promo@shop.example>",
                "To": DEFAULT_USER,
                "Subject": "FLASH SALE - 50% off everything",
                **{k.title(): v for k, v in bulk_headers().items()},
            },
            plain_body="Shop now. Unsubscribe here.",
        ),
        gmail_message(
            message_id="m3",
            headers={
                "From": "Friend <friend@example.com>",
                "To": DEFAULT_USER,
                "Subject": "dinner saturday?",
            },
            plain_body="Are you free?",
        ),
    ]


@pytest.fixture
def fake_gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmailClient:
    client = FakeGmailClient(_sample_messages())
    monkeypatch.setattr("app.gmail.client.get_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _no_live_google(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never reach Contacts or Sheets from these tests."""
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: (_ for _ in ()).throw(
            RuntimeError("workbook unavailable")
        )),
    )


# --------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------


def test_context_degrades_gracefully_without_contacts_or_workbook() -> None:
    """A missing data source must thin the context, never break the run."""
    context = build_live_context(user_email=DEFAULT_USER)

    assert context.user_email == DEFAULT_USER
    assert context.known_contacts == set()
    assert context.sender_rules == {}


def test_context_can_skip_both_sources() -> None:
    context = build_live_context(
        include_contacts=False, include_workbook=False, user_email=DEFAULT_USER
    )

    assert context.known_contacts == set()
    assert context.vip_emails == set()


# --------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------


def test_preview_classifies_each_message(fake_gmail) -> None:
    results = preview_recent(limit=10)

    assert len(results) == 3
    by_id = {r.message.message_id: r.classification for r in results}

    assert by_id["m1"].protected
    assert not by_id["m1"].review
    assert by_id["m2"].review
    assert not by_id["m3"].review


def test_preview_limit_is_capped(fake_gmail) -> None:
    preview_recent(limit=10_000)
    assert fake_gmail.requested_limits[-1] == MAX_PREVIEW_MESSAGES


def test_preview_limit_has_a_floor(fake_gmail) -> None:
    preview_recent(limit=0)
    assert fake_gmail.requested_limits[-1] == 1


def test_preview_result_view_excludes_the_body(fake_gmail) -> None:
    results = preview_recent(limit=1)
    view = results[0].as_dict()

    assert "body" not in view
    assert "body_text" not in view
    assert view["subject"] == "Your account statement is ready"
    assert view["from"] == "alerts@chase.com"
    assert "August statement" not in str(view)


def test_preview_view_reports_intent_not_action(fake_gmail) -> None:
    view = preview_recent(limit=1)[0].as_dict()

    # Every placement field is phrased as a proposal.
    assert "would_keep_in_inbox" in view
    assert "would_archive" in view
    assert "would_review" in view
    assert view["why"]


def test_summary_counts(fake_gmail) -> None:
    summary = summarize(preview_recent(limit=10))

    assert summary["total"] == 3
    assert summary["would_review"] == 1
    assert summary["protected_routed_to_review"] == 0
    assert sum(summary["by_priority"].values()) == 3


def test_summary_tracks_the_launch_gate_metric() -> None:
    """The §15 metric must count real violations, not always report zero."""
    protected_and_reviewed = Classification(
        message_id="x", protected=True, review=True, keep_in_inbox=False
    )
    results = [
        PreviewResult(message=make_message(), classification=protected_and_reviewed)
    ]

    assert summarize(results)["protected_routed_to_review"] == 1


# --------------------------------------------------------------------
# Route
# --------------------------------------------------------------------


def test_preview_route_requires_a_connected_account(client: TestClient) -> None:
    resp = client.get("/classify/preview")

    assert resp.status_code == 409
    assert "oauth/start" in resp.json()["detail"].lower()


def test_preview_route_returns_decisions(client: TestClient, fake_gmail) -> None:
    save_token(
        StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER)
    )

    body = client.get("/classify/preview?limit=10").json()

    assert body["gmail_modified"] is False
    assert body["dry_run"] is True
    assert body["summary"]["total"] == 3
    assert body["summary"]["protected_routed_to_review"] == 0
    assert len(body["messages"]) == 3


def test_preview_route_states_that_nothing_changed(
    client: TestClient, fake_gmail
) -> None:
    save_token(StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES)))

    body = client.get("/classify/preview?limit=1").json()

    assert "nothing in your gmail has been changed" in body["note"].lower()


def test_classification_never_calls_a_gmail_write_method(fake_gmail) -> None:
    """The read client used by the pipeline exposes no mutating verbs."""
    from app.gmail.client import GmailReadClient

    forbidden = {"send", "modify", "trash", "untrash", "delete", "batchdelete", "archive"}
    for attribute in dir(GmailReadClient):
        assert not any(word in attribute.lower() for word in forbidden), attribute
