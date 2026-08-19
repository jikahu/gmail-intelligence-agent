"""Undo Last Run routes (Phase 12): the dashboard confirm-then-undo pages
and their JSON mirrors.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dashboard import auth
from app.gmail import apply as gmail_apply
from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import DEFAULT_USER
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


class FakeWriteClient:
    def __init__(self) -> None:
        self.modify_calls: list[tuple[str, list[str], list[str]]] = []
        self.untrash_calls: list[str] = []

    def modify_message(self, message_id, *, add_label_ids=None, remove_label_ids=None):
        self.modify_calls.append(
            (message_id, list(add_label_ids or []), list(remove_label_ids or []))
        )
        return {"id": message_id}

    def untrash_message(self, message_id):
        self.untrash_calls.append(message_id)
        return {"id": message_id}


@pytest.fixture
def undo_wired(monkeypatch: pytest.MonkeyPatch):
    save_token(
        StoredToken(
            refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER
        )
    )
    write_client = FakeWriteClient()
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: write_client)
    monkeypatch.setattr(gmail_apply, "fetch_current_labels", lambda message_id: ["AI/Review", "INBOX"])
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
    return wb, write_client


def _open_gate(monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    get_settings.cache_clear()
    workbook.settings.set("last_acceptance_passed", "true")


def _record_undoable_run(workbook: ControlWorkbook, run_id: str = "run-1") -> None:
    workbook.system_runs.record(
        run_id=run_id, mode="live", started_at="t0", completed_at="t1",
        emails_processed=1, emails_changed=1, undo_available=True,
    )
    workbook.audit_log.record_many([
        {
            "event_id": "e1", "run_id": run_id, "timestamp": "t1",
            "gmail_message_id": "m1", "thread_id": "th1",
            "subject_safe_ref": "50% off sale!", "action_taken": "restored to Inbox",
            "labels_before": "AI/Review", "labels_after": "AI/Review, INBOX",
            "actor": "user", "reversible": "true",
        }
    ])


def _sign_in(client: TestClient) -> None:
    client.cookies.set(auth.SESSION_COOKIE, auth.issue_session(DEFAULT_USER))


# --------------------------------------------------------------------
# Dashboard preview page
# --------------------------------------------------------------------


def test_undo_preview_requires_sign_in(client: TestClient) -> None:
    resp = client.get("/dashboard/undo", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


def test_undo_preview_says_nothing_to_undo(client: TestClient, undo_wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard/undo")
    assert resp.status_code == 200
    assert "Nothing to undo" in resp.text


def test_undo_preview_shows_the_run_and_does_not_undo_anything(
    client: TestClient, undo_wired
) -> None:
    workbook, write_client = undo_wired
    _record_undoable_run(workbook)
    _sign_in(client)

    resp = client.get("/dashboard/undo")
    assert resp.status_code == 200
    assert "50% off sale!" in resp.text
    assert 'action="/dashboard/undo"' in resp.text
    assert write_client.modify_calls == []
    assert write_client.untrash_calls == []


# --------------------------------------------------------------------
# Dashboard confirm action
# --------------------------------------------------------------------


def test_undo_confirm_refuses_when_gate_closed(client: TestClient, undo_wired) -> None:
    workbook, write_client = undo_wired
    _record_undoable_run(workbook)
    _sign_in(client)

    resp = client.post("/dashboard/undo", data={"run_id": "run-1"})
    assert resp.status_code == 200
    assert "Couldn" in resp.text  # "Couldn't undo"
    assert write_client.modify_calls == []


def test_undo_confirm_restores_the_run_when_gate_open(
    client: TestClient, undo_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook, write_client = undo_wired
    _record_undoable_run(workbook)
    _open_gate(monkeypatch, workbook)
    _sign_in(client)

    resp = client.post("/dashboard/undo", data={"run_id": "run-1"})
    assert resp.status_code == 200
    assert "Undo complete" in resp.text
    assert write_client.modify_calls == [("m1", [], ["INBOX"])]

    # A second visit finds nothing left to undo.
    resp2 = client.get("/dashboard/undo")
    assert "Nothing to undo" in resp2.text


# --------------------------------------------------------------------
# JSON mirrors
# --------------------------------------------------------------------


def test_undo_preview_json_reports_unavailable(client: TestClient, undo_wired) -> None:
    resp = client.get("/undo/preview")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_undo_preview_json_reports_the_run(client: TestClient, undo_wired) -> None:
    workbook, _write_client = undo_wired
    _record_undoable_run(workbook)
    resp = client.get("/undo/preview")
    body = resp.json()
    assert body["available"] is True
    assert body["run_id"] == "run-1"
    assert body["message_count"] == 1


def test_undo_run_json_requires_explicit_confirm(client: TestClient, undo_wired) -> None:
    workbook, write_client = undo_wired
    _record_undoable_run(workbook)
    resp = client.post("/undo/run", params={"run_id": "run-1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_confirmed"
    assert write_client.modify_calls == []


def test_undo_run_json_executes_when_confirmed_and_gate_open(
    client: TestClient, undo_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook, write_client = undo_wired
    _record_undoable_run(workbook)
    _open_gate(monkeypatch, workbook)

    resp = client.post("/undo/run", params={"run_id": "run-1", "confirm": "true"})
    body = resp.json()
    assert body["status"] == "done"
    assert body["restored_count"] == 1
    assert write_client.modify_calls == [("m1", [], ["INBOX"])]
