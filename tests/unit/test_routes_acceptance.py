"""The /acceptance/run route and its dashboard report views (Phase 10)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.acceptance import service as acceptance_service
from app.dashboard import auth
from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, gmail_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    acceptance_service.reset_cache()
    yield
    acceptance_service.reset_cache()


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
            message_id="promo",
            headers={
                "From": "deals@shop.example",
                "To": DEFAULT_USER,
                "Subject": "50% off everything!",
                **bulk_headers(),
            },
            plain_body="Huge sale. Unsubscribe here.",
        ),
        gmail_message(
            message_id="work1",
            headers={"From": "colleague@company.example", "To": DEFAULT_USER, "Subject": "Project update"},
            plain_body="Sharing the latest status.",
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
        StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER)
    )


def _sign_in(client: TestClient) -> None:
    client.cookies.set(auth.SESSION_COOKIE, auth.issue_session(DEFAULT_USER))


# --------------------------------------------------------------------
# POST /acceptance/run
# --------------------------------------------------------------------


def test_acceptance_run_requires_connection(client: TestClient) -> None:
    assert client.post("/acceptance/run").status_code == 409


def test_acceptance_run_preview_only(client: TestClient, fake_gmail: FakeGmailClient) -> None:
    _connect()
    body = client.post(
        "/acceptance/run?target=20&use_ai=false&read_attachments=false&persist=false"
    ).json()

    assert body["gmail_modified"] is False
    assert body["persisted"] is False
    assert body["sample_size"] == 2
    assert body["target_size"] == 20
    assert body["passed"] is True
    assert body["false_reviews"] == []
    assert body["dashboard_url"].startswith("/dashboard/acceptance/")


def test_acceptance_run_persists_to_the_workbook(
    client: TestClient, fake_gmail: FakeGmailClient, fake_workbook: ControlWorkbook
) -> None:
    _connect()
    body = client.post(
        "/acceptance/run?target=20&use_ai=false&read_attachments=false&persist=true"
    ).json()

    assert body["persisted"] is True
    run_row = fake_workbook.system_runs.for_run(body["run_id"])
    assert run_row is not None
    assert fake_workbook.settings.get("last_acceptance_passed") == "true"


# --------------------------------------------------------------------
# GET /dashboard/acceptance[/{run_id}]
# --------------------------------------------------------------------


def test_dashboard_acceptance_requires_sign_in(client: TestClient) -> None:
    resp = client.get("/dashboard/acceptance", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


def test_dashboard_acceptance_with_no_runs_yet(client: TestClient) -> None:
    _connect()
    _sign_in(client)
    resp = client.get("/dashboard/acceptance")
    assert resp.status_code == 200
    assert "No acceptance run yet" in resp.text


def test_dashboard_acceptance_shows_the_latest_run(
    client: TestClient, fake_gmail: FakeGmailClient
) -> None:
    _connect()
    run_body = client.post(
        "/acceptance/run?target=20&use_ai=false&read_attachments=false&persist=false"
    ).json()

    _sign_in(client)
    resp = client.get("/dashboard/acceptance")
    assert resp.status_code == 200
    assert run_body["run_id"] in resp.text
    assert "PASSED" in resp.text
    assert "deals@shop.example" in resp.text  # the Review-queue row shown for human eyeballing


def test_dashboard_acceptance_specific_run(client: TestClient, fake_gmail: FakeGmailClient) -> None:
    _connect()
    run_body = client.post(
        "/acceptance/run?target=20&use_ai=false&read_attachments=false&persist=false"
    ).json()

    _sign_in(client)
    resp = client.get(f"/dashboard/acceptance/{run_body['run_id']}")
    assert resp.status_code == 200
    assert run_body["run_id"] in resp.text


def test_dashboard_acceptance_unknown_run_is_404(client: TestClient) -> None:
    _connect()
    _sign_in(client)
    resp = client.get("/dashboard/acceptance/not-a-real-run-id")
    assert resp.status_code == 404
