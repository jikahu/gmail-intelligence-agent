"""The /followup/scan route (Phase 7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from tests.fixtures.emails import DEFAULT_USER, gmail_message


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
            message_id="soon1",
            headers={
                "From": "Utility <billing@utility.com>",
                "To": DEFAULT_USER,
                "Subject": "Your bill",
            },
            # "tomorrow" is always within 3 business days → due_soon, whatever
            # the real date the test runs on.
            plain_body="Payment due tomorrow. Amount due $40.",
        ),
        gmail_message(
            message_id="late1",
            headers={
                "From": "Utility <billing@utility.com>",
                "To": DEFAULT_USER,
                "Subject": "Overdue notice",
            },
            plain_body="Payment due January 1, 2020. Please pay.",
        ),
    ]


@pytest.fixture
def fake_gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmailClient:
    client = FakeGmailClient(_messages())
    monkeypatch.setattr("app.gmail.client.get_client", lambda: client)
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
    return client


def _connect() -> None:
    save_token(
        StoredToken(
            refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER
        )
    )


def test_followup_scan_returns_due_soon_and_overdue(client: TestClient, fake_gmail) -> None:
    _connect()
    body = client.post("/followup/scan?limit=10").json()

    assert body["gmail_modified"] is False
    assert body["persisted"] is False
    summary = body["followups"]["summary"]
    assert summary["due_soon"] >= 1
    assert summary["overdue_deadlines"] >= 1


def test_followup_scan_requires_connection(client: TestClient) -> None:
    assert client.post("/followup/scan").status_code == 409


def test_followup_scan_persists_refined_statuses(
    client: TestClient, fake_gmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connect()

    class _Repo:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        def upsert(self, values):  # noqa: ANN001
            self.rows.append(dict(values))
            return "inserted"

    class _FakeWorkbook:
        def __init__(self) -> None:
            self.deadlines = _Repo()
            self.subscriptions = _Repo()
            self.trips = _Repo()

    fake_wb = _FakeWorkbook()
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: fake_wb),
    )

    body = client.post("/followup/scan?limit=10&persist=true").json()

    assert body["persisted"] is True
    assert body["written"]["deadlines"]["inserted"] >= 1
    # The persisted statuses are the sharpened ones.
    statuses = {row.get("status") for row in fake_wb.deadlines.rows}
    assert "due_soon" in statuses or "overdue" in statuses
