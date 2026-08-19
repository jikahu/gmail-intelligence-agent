"""The Phase 6 routes: /classify/preview intelligence, and /intelligence/scan."""

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
            message_id="sub1",
            headers={
                "From": "Netflix <billing@netflix.com>",
                "To": DEFAULT_USER,
                "Subject": "Your subscription renews soon",
            },
            plain_body="Your plan is $15.99 per month and renews on September 1, 2026.",
        ),
        gmail_message(
            message_id="bill1",
            headers={
                "From": "Chase <statements@chase.com>",
                "To": DEFAULT_USER,
                "Subject": "Your statement is ready",
            },
            plain_body="Amount due $50.00, payment due by September 20, 2026.",
        ),
    ]


@pytest.fixture
def fake_gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmailClient:
    client = FakeGmailClient(_messages())
    monkeypatch.setattr("app.gmail.client.get_client", lambda: client)
    # Keep Contacts and the workbook rules out of these route tests.
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


# -------- /classify/preview now carries intelligence --------


def test_preview_includes_intelligence_block(client: TestClient, fake_gmail) -> None:
    _connect()
    body = client.get("/classify/preview?limit=10").json()

    assert "intelligence" in body
    assert "summary" in body["intelligence"]
    assert body["intelligence"]["summary"]["subscriptions"] >= 1
    assert body["intelligence"]["summary"]["deadlines"] >= 1
    # And it changed nothing.
    assert body["gmail_modified"] is False


# -------- /intelligence/scan --------


def test_scan_preview_only_writes_nothing(client: TestClient, fake_gmail) -> None:
    _connect()
    body = client.post("/intelligence/scan?limit=10&persist=false").json()

    assert body["gmail_modified"] is False
    assert body["persisted"] is False
    assert body["written"] is None
    assert body["intelligence"]["summary"]["subscriptions"] >= 1


def test_scan_requires_a_connected_account(client: TestClient) -> None:
    resp = client.post("/intelligence/scan")
    assert resp.status_code == 409


def test_scan_persists_through_the_repository(
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

    body = client.post("/intelligence/scan?limit=10&persist=true").json()

    assert body["gmail_modified"] is False
    assert body["persisted"] is True
    assert body["written"]["subscriptions"]["inserted"] >= 1
    assert body["written"]["deadlines"]["inserted"] >= 1
    # The rows really went through the repository, not Gmail.
    assert fake_wb.subscriptions.rows
    assert any(row.get("service") == "Netflix" for row in fake_wb.subscriptions.rows)
