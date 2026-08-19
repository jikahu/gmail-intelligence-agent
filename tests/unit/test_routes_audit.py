"""The /audit/scan route (Phase 9)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from app.sheets.repository import ControlWorkbook
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
            message_id="p1",
            headers={
                "From": "Bank <alerts@bank.com>",
                "To": DEFAULT_USER,
                "Subject": "Action needed: payment failed",
            },
            plain_body="Your payment failed and your card was declined.",
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


def test_audit_scan_requires_connection(client: TestClient) -> None:
    assert client.post("/audit/scan").status_code == 409


def test_audit_scan_persists_one_row_per_message(
    client: TestClient, fake_gmail, fake_workbook: ControlWorkbook
) -> None:
    _connect()
    body = client.post("/audit/scan?limit=10&ai=false&attachments=false").json()

    assert body["gmail_modified"] is False
    assert body["persisted"] is True
    assert body["count"] == 1
    assert body["run_id"]

    rows = fake_workbook.audit_log.all()
    assert len(rows) == 1
    assert rows[0].get("run_id") == body["run_id"]
    assert rows[0].get("gmail_message_id") == "p1"
    assert rows[0].get("action_taken", "").startswith("proposed only")


def test_audit_scan_preview_only_writes_nothing(
    client: TestClient, fake_gmail, fake_workbook: ControlWorkbook
) -> None:
    _connect()
    body = client.post("/audit/scan?limit=10&ai=false&attachments=false&persist=false").json()

    assert body["persisted"] is False
    assert body["run_id"] is None
    assert fake_workbook.audit_log.all() == []
