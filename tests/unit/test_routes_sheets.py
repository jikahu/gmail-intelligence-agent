"""HTTP route tests for the Sheets control workbook — no real Google calls."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gmail.scopes import PHASE_1_SCOPES
from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from app.sheets.workbook import WorkbookInfo
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


def _store_token(scopes: list[str]) -> None:
    save_token(
        StoredToken(
            refresh_token="1//refresh",
            access_token="ya29.access",
            scopes=scopes,
            account_email="jikahu@gmail.com",
        )
    )


@pytest.fixture
def connected_client(client: TestClient) -> TestClient:
    """A client whose stored token has the full Phase 2 grant."""
    _store_token(list(ACTIVE_SCOPES))
    return client


@pytest.fixture
def stale_client(client: TestClient) -> TestClient:
    """A client whose token predates the Sheets scopes."""
    _store_token(list(PHASE_1_SCOPES))
    return client


# --------------------------------------------------------------------
# /sheets/status
# --------------------------------------------------------------------


def test_status_reports_disconnected_without_a_token(client: TestClient) -> None:
    body = client.get("/sheets/status").json()
    assert body["connected"] is False
    assert body["initialized"] is False


def test_status_flags_a_stale_grant(stale_client: TestClient) -> None:
    body = stale_client.get("/sheets/status").json()
    assert body["connected"] is True
    assert body["reconnect_required"] is True
    assert "https://www.googleapis.com/auth/spreadsheets" in body["missing_scopes"]
    assert body["initialized"] is False


def test_status_reports_uninitialized_when_drive_is_empty(
    connected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.sheets.client.get_drive_service", lambda: FakeDriveService()
    )
    body = connected_client.get("/sheets/status").json()

    assert body["connected"] is True
    assert body["reconnect_required"] is False
    assert body["initialized"] is False
    assert body["workbook_id"] is None


def test_status_finds_an_existing_workbook(
    connected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDriveService(
        files=[{"id": "sheet-abc", "name": "Gmail Agent Control Workbook"}]
    )
    monkeypatch.setattr("app.sheets.client.get_drive_service", lambda: drive)

    body = connected_client.get("/sheets/status").json()

    assert body["initialized"] is True
    assert body["workbook_id"] == "sheet-abc"
    assert body["workbook_url"].endswith("/sheet-abc/edit")


# --------------------------------------------------------------------
# /sheets/init
# --------------------------------------------------------------------


def test_init_refuses_a_stale_grant(stale_client: TestClient) -> None:
    resp = stale_client.post("/sheets/init")
    assert resp.status_code == 409
    assert "reconnect" in resp.json()["detail"].lower()


def test_init_requires_a_connected_account(client: TestClient) -> None:
    resp = client.post("/sheets/init")
    assert resp.status_code == 409
    assert "oauth/start" in resp.json()["detail"].lower()


def test_init_reports_what_it_built(
    connected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.sheets.workbook.get_sheets_service", lambda: FakeSheetsService()
    )
    monkeypatch.setattr(
        "app.sheets.workbook.get_drive_service", lambda: FakeDriveService()
    )

    body = connected_client.post("/sheets/init").json()

    assert body["created"] is True
    assert body["settings_seeded"] is True
    assert body["changed"] is True
    assert body["workbook_id"]
    assert body["workbook_url"].endswith("/edit")


def test_init_is_idempotent_from_the_route(
    connected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    monkeypatch.setattr("app.sheets.workbook.get_sheets_service", lambda: sheets)
    monkeypatch.setattr("app.sheets.workbook.get_drive_service", lambda: drive)

    first = connected_client.post("/sheets/init").json()
    drive.files_present.append(
        {"id": first["workbook_id"], "name": "Gmail Agent Control Workbook"}
    )
    second = connected_client.post("/sheets/init").json()

    assert second["workbook_id"] == first["workbook_id"]
    assert second["created"] is False
    assert second["changed"] is False
    assert sheets.call_counts["create"] == 1


def test_init_surfaces_a_disconnect_mid_flight(
    connected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.google_api import NotConnectedError

    def _boom(**_kwargs) -> WorkbookInfo:
        raise NotConnectedError("No Google token found. Visit /oauth/start.")

    monkeypatch.setattr("app.sheets.workbook.ensure_workbook", _boom)

    resp = connected_client.post("/sheets/init")
    assert resp.status_code == 409


# --------------------------------------------------------------------
# /sheets/settings
# --------------------------------------------------------------------


def test_settings_route_returns_the_control_panel(
    connected_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = FakeSheetsService()
    monkeypatch.setattr("app.sheets.repository.get_sheets_service", lambda: sheets)
    monkeypatch.setattr(
        "app.sheets.workbook.get_drive_service", lambda: FakeDriveService()
    )

    body = connected_client.get("/sheets/settings").json()

    assert body["settings"]["dry_run"] == "true"
    assert body["settings"]["gmail_processing_enabled"] == "false"
    assert body["workbook_url"].endswith("/edit")


def test_settings_route_refuses_a_stale_grant(stale_client: TestClient) -> None:
    assert stale_client.get("/sheets/settings").status_code == 409


# --------------------------------------------------------------------
# Landing page
# --------------------------------------------------------------------


def test_consent_screen_lists_the_sheets_permissions(client: TestClient) -> None:
    text = client.get("/").text
    assert "spreadsheets" in text
    assert "drive.file" in text
    assert "Does NOT grant access to the rest of your Google Drive" in text


def test_landing_page_warns_when_reconnect_is_needed(stale_client: TestClient) -> None:
    text = stale_client.get("/").text
    assert "Reconnect required" in text
    assert "Create / update my control workbook" not in text


def test_landing_page_offers_the_workbook_when_fully_granted(
    connected_client: TestClient,
) -> None:
    text = connected_client.get("/").text
    assert "Create / update my control workbook" in text
    assert "Reconnect required" not in text


def test_health_reports_reconnect_state(stale_client: TestClient) -> None:
    assert stale_client.get("/health").json()["reconnect_required"] is True
