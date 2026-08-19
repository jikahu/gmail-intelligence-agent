"""The Phase 14 digest routes: GET /digest/status, POST /digest/scan, and
the dashboard's GET /dashboard/digest page.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dashboard import auth
from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, gmail_message
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
        gmail_message(
            message_id="xss",
            headers={
                "From": "Deals <deals@shop.example>",
                "To": DEFAULT_USER,
                "Subject": "<script>alert(1)</script> 50% off sale!",
                **bulk_headers(),
            },
            plain_body="Huge limited-time sale. Unsubscribe here.",
        ),
    ]


def _sign_in(client: TestClient) -> None:
    client.cookies.set(auth.SESSION_COOKIE, auth.issue_session(DEFAULT_USER))


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connected Gmail account, no real workbook (degrades gracefully)."""
    save_token(
        StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER)
    )
    monkeypatch.setattr("app.gmail.client.get_client", lambda: FakeGmailClient(_messages()))
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(
            lambda cls, spreadsheet_id=None: (_ for _ in ()).throw(
                RuntimeError("workbook unavailable")
            )
        ),
    )


@pytest.fixture
def wired_with_workbook(monkeypatch: pytest.MonkeyPatch) -> ControlWorkbook:
    """Connected Gmail account plus a real fake workbook writes actually land in."""
    save_token(
        StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER)
    )
    monkeypatch.setattr("app.gmail.client.get_client", lambda: FakeGmailClient(_messages()))
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    wb = ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: wb),
    )
    return wb


# --------------------------------------------------------------------
# /digest/status
# --------------------------------------------------------------------


def test_status_reports_enabled_by_default(client: TestClient) -> None:
    resp = client.get("/digest/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["running"] is False
    assert body["check_count"] == 0
    assert "digest_hour" in body
    assert "digest_timezone" in body


# --------------------------------------------------------------------
# /digest/scan
# --------------------------------------------------------------------


def test_scan_refuses_without_a_connected_account(client: TestClient) -> None:
    resp = client.post("/digest/scan")
    assert resp.status_code == 409


def test_scan_previews_without_persisting(client: TestClient, wired_with_workbook) -> None:
    wb = wired_with_workbook
    resp = client.post("/digest/scan?persist=false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gmail_modified"] is False
    assert body["persisted"] is False
    assert body["written"] is None
    assert wb.digest_log.all() == []


def test_scan_persists_a_digest_log_row(client: TestClient, wired_with_workbook) -> None:
    wb = wired_with_workbook
    resp = client.post("/digest/scan?persist=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted"] is True
    assert body["written"]["action"] == "inserted"

    rows = wb.digest_log.all()
    assert len(rows) == 1
    assert rows[0].get("total_count") is not None


def test_scan_response_has_digest_sections_in_order(client: TestClient, wired_with_workbook) -> None:
    resp = client.post("/digest/scan?persist=false")
    body = resp.json()
    keys = [s["key"] for s in body["digest"]["sections"]]
    assert keys == ["p1", "p2", "action", "overdue", "waiting", "due_soon", "review"]


# --------------------------------------------------------------------
# /dashboard/digest
# --------------------------------------------------------------------


def test_dashboard_digest_redirects_when_signed_out(client: TestClient) -> None:
    resp = client.get("/dashboard/digest", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/login")


def test_dashboard_digest_renders_for_a_signed_in_user(client: TestClient, wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard/digest")
    assert resp.status_code == 200
    assert "Daily Digest" in resp.text
    assert "Action needed: payment failed" in resp.text


def test_dashboard_digest_escapes_untrusted_subject_text(client: TestClient, wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard/digest")
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_dashboard_digest_dry_run_banner_shown_by_default(client: TestClient, wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard/digest")
    assert "DRY RUN" in resp.text
