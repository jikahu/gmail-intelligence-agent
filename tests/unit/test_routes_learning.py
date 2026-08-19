"""The /learning/suggest-vips and /learning/promote-suggestions routes (Phase 9)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from app.sheets.repository import ControlWorkbook
from app.sheets.schema import LEARNED_RULE_SUGGESTIONS_TAB
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import DEFAULT_USER, gmail_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


class FakeGmailClient:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages

    def get_profile(self) -> dict:
        return {"emailAddress": DEFAULT_USER}

    def list_recent_messages(self, max_results: int = 10, query: str | None = None):
        return self._messages[:max_results]


def _messages() -> list[dict]:
    return [
        gmail_message(
            message_id=f"m{i}",
            headers={
                "From": "Colleague <colleague@work.com>",
                "To": DEFAULT_USER,
                "Subject": "Re: project status",
            },
            plain_body="Following up on the project.",
        )
        for i in range(3)
    ]


@pytest.fixture
def fake_gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmailClient:
    client = FakeGmailClient(_messages())
    monkeypatch.setattr("app.gmail.client.get_client", lambda: client)
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    return client


@pytest.fixture
def fake_workbook(monkeypatch: pytest.MonkeyPatch) -> ControlWorkbook:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    wb = ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: wb),
    )
    return wb


def _connect() -> None:
    save_token(
        StoredToken(
            refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER
        )
    )


# --------------------------------------------------------------------
# /learning/suggest-vips
# --------------------------------------------------------------------


def test_suggest_vips_requires_connection(client: TestClient) -> None:
    assert client.post("/learning/suggest-vips").status_code == 409


def test_suggest_vips_persists_a_frequent_correspondent(
    client: TestClient, fake_gmail, fake_workbook: ControlWorkbook
) -> None:
    _connect()
    body = client.post("/learning/suggest-vips?limit=10").json()

    assert body["gmail_modified"] is False
    assert body["persisted"] is True
    assert "colleague@work.com" in body["suggested"]
    assert {v.email for v in fake_workbook.vips.suggested()} == {"colleague@work.com"}
    # Still just a suggestion — never approved automatically.
    assert fake_workbook.vips.approved_emails() == set()


def test_suggest_vips_preview_only_writes_nothing(
    client: TestClient, fake_gmail, fake_workbook: ControlWorkbook
) -> None:
    _connect()
    body = client.post("/learning/suggest-vips?limit=10&persist=false").json()

    assert body["persisted"] is False
    assert body["suggested"] == []
    assert fake_workbook.vips.suggested() == []


# --------------------------------------------------------------------
# /learning/promote-suggestions
# --------------------------------------------------------------------


def test_promote_suggestions_requires_connection(client: TestClient) -> None:
    assert client.post("/learning/promote-suggestions").status_code == 409


def test_promote_suggestions_activates_approved_rules(
    client: TestClient, fake_workbook: ControlWorkbook
) -> None:
    _connect()
    sid = fake_workbook.rules.add_rule_suggestion(
        target="friend@example.com", suggested_rule="whitelist", evidence="x", confidence=1.0
    )
    table = fake_workbook.table(LEARNED_RULE_SUGGESTIONS_TAB)
    table.update(table.first(suggestion_id=sid), {"status": "approved"})

    body = client.post("/learning/promote-suggestions").json()

    assert body["gmail_modified"] is False
    assert body["promoted"] == ["friend@example.com"]
    assert [r.sender for r in fake_workbook.rules.get_sender_rules()] == ["friend@example.com"]


def test_promote_suggestions_ignores_unapproved_ones(
    client: TestClient, fake_workbook: ControlWorkbook
) -> None:
    _connect()
    fake_workbook.rules.add_rule_suggestion(
        target="unreviewed@example.com", suggested_rule="whitelist", evidence="x", confidence=1.0
    )

    body = client.post("/learning/promote-suggestions").json()

    assert body["promoted"] == []
    assert fake_workbook.rules.get_sender_rules() == []
