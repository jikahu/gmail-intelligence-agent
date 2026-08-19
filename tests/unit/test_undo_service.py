"""Undo Last Run (Phase 12) — restoration, not replay, and always through
the same write gate as every other Gmail write.
"""

from __future__ import annotations

import pytest

from app.gmail import apply as gmail_apply
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from app.undo import service as undo_service
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


@pytest.fixture
def workbook() -> ControlWorkbook:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    return ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)


def _open_gate(monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    get_settings.cache_clear()
    workbook.settings.set("last_acceptance_passed", "true")


def _record_a_restore_run(workbook: ControlWorkbook, run_id: str = "run-1") -> None:
    """Simulate what Phase 11's dashboard_ops.restore_to_inbox would have
    written: a message moved out of AI/Review-only state into +INBOX."""
    workbook.system_runs.record(
        run_id=run_id, mode="live", started_at="t0", completed_at="t1",
        emails_processed=1, emails_changed=1, undo_available=True,
    )
    workbook.audit_log.record_many([
        {
            "event_id": "e1", "run_id": run_id, "timestamp": "t1",
            "gmail_message_id": "m1", "thread_id": "th1",
            "subject_safe_ref": "50% off sale!", "classification": "AI/Review",
            "action_taken": "restored to Inbox",
            "labels_before": "AI/Review", "labels_after": "AI/Review, INBOX",
            "inbox_before": "false", "inbox_after": "true",
            "actor": "user", "reversible": "true", "undo_status": "not_undone",
        }
    ])


class FakeWriteClient:
    def __init__(self) -> None:
        self.modify_calls: list[tuple[str, list[str], list[str]]] = []
        self.untrash_calls: list[str] = []

    def modify_message(self, message_id, *, add_label_ids=None, remove_label_ids=None):
        self.modify_calls.append((message_id, list(add_label_ids or []), list(remove_label_ids or [])))
        return {"id": message_id}

    def untrash_message(self, message_id):
        self.untrash_calls.append(message_id)
        return {"id": message_id}


# --------------------------------------------------------------------
# preview
# --------------------------------------------------------------------


def test_preview_last_run_is_none_when_nothing_undoable(workbook) -> None:
    assert undo_service.preview_last_run(workbook) is None


def test_preview_last_run_describes_the_undoable_run(workbook) -> None:
    _record_a_restore_run(workbook)
    preview = undo_service.preview_last_run(workbook)
    assert preview is not None
    assert preview.run_id == "run-1"
    assert preview.message_count == 1
    assert preview.messages[0].message_id == "m1"
    assert preview.messages[0].labels_before == ["AI/Review"]
    assert preview.messages[0].labels_after == ["AI/Review", "INBOX"]


def test_preview_excludes_non_reversible_rows(workbook) -> None:
    workbook.system_runs.record(
        run_id="run-2", mode="dry_run", started_at="t0", completed_at="t1",
        emails_processed=1, undo_available=True,
    )
    workbook.audit_log.record_many([
        {
            "event_id": "e2", "run_id": "run-2", "timestamp": "t1",
            "gmail_message_id": "m2", "action_taken": "proposed only — dry run",
            "labels_before": "AI/Review", "labels_after": "AI/Review",
            "reversible": "false",
        }
    ])
    preview = undo_service.preview_run(workbook, "run-2")
    assert preview is not None
    assert preview.message_count == 0


# --------------------------------------------------------------------
# undo_run
# --------------------------------------------------------------------


def test_undo_refuses_when_gate_is_closed(workbook, monkeypatch: pytest.MonkeyPatch) -> None:
    _record_a_restore_run(workbook)
    result = undo_service.undo_run(workbook, "run-1")
    assert result.status == "gate_closed"
    assert result.gate_reasons
    # Nothing should have been marked undone — the run is still available.
    assert workbook.system_runs.latest_undoable() is not None


def test_undo_reports_not_found_for_an_unknown_run(workbook, monkeypatch: pytest.MonkeyPatch) -> None:
    _open_gate(monkeypatch, workbook)
    result = undo_service.undo_run(workbook, "no-such-run")
    assert result.status == "not_found"


def test_undo_restores_labels_and_inbox_state(
    workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_gate(monkeypatch, workbook)
    _record_a_restore_run(workbook)

    fake_client = FakeWriteClient()
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: fake_client)
    monkeypatch.setattr(
        gmail_apply, "fetch_current_labels", lambda message_id: ["AI/Review", "INBOX"]
    )

    result = undo_service.undo_run(workbook, "run-1")

    assert result.status == "done"
    assert result.restored_count == 1
    assert fake_client.modify_calls == [("m1", [], ["INBOX"])]
    assert fake_client.untrash_calls == []

    # The run is no longer offered for undo a second time.
    assert workbook.system_runs.latest_undoable() is None
    # A new, honest audit row records the undo itself.
    rows = workbook.audit_log.for_run("run-1")
    undo_rows = [r for r in rows if r.get("action_taken", "").startswith("Undo Last Run")]
    assert len(undo_rows) == 1
    assert undo_rows[0].get("actor") == "user"


def test_undo_is_idempotent_when_already_in_the_target_state(
    workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_gate(monkeypatch, workbook)
    _record_a_restore_run(workbook)

    fake_client = FakeWriteClient()
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: fake_client)
    # Someone already manually removed INBOX — current state already matches "before".
    monkeypatch.setattr(gmail_apply, "fetch_current_labels", lambda message_id: ["AI/Review"])

    result = undo_service.undo_run(workbook, "run-1")

    assert result.status == "done"
    assert result.outcomes[0].outcome == "already_ok"
    assert fake_client.modify_calls == []


def test_undo_restores_a_trashed_message(workbook, monkeypatch: pytest.MonkeyPatch) -> None:
    _open_gate(monkeypatch, workbook)
    workbook.system_runs.record(
        run_id="run-trash", mode="live", started_at="t0", completed_at="t1",
        emails_processed=1, emails_changed=1, undo_available=True,
    )
    workbook.audit_log.record_many([
        {
            "event_id": "e3", "run_id": "run-trash", "timestamp": "t1",
            "gmail_message_id": "m3", "thread_id": "th3",
            "subject_safe_ref": "Old newsletter", "action_taken": "moved to Gmail Trash",
            "labels_before": "AI/Review, INBOX", "labels_after": "TRASH",
            "actor": "user", "reversible": "true",
        }
    ])

    fake_client = FakeWriteClient()
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: fake_client)
    monkeypatch.setattr(gmail_apply, "fetch_current_labels", lambda message_id: ["TRASH"])

    result = undo_service.undo_run(workbook, "run-trash")

    assert result.status == "done"
    assert fake_client.untrash_calls == ["m3"]
    assert fake_client.modify_calls == []


def test_undo_reports_a_message_no_longer_in_gmail_without_crashing(
    workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    from googleapiclient.errors import HttpError

    _open_gate(monkeypatch, workbook)
    _record_a_restore_run(workbook)

    class _FakeResp:
        status = 404
        reason = "Not Found"

    def _raise_404(message_id: str) -> list[str]:
        raise HttpError(_FakeResp(), b"not found")

    fake_client = FakeWriteClient()
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: fake_client)
    monkeypatch.setattr(gmail_apply, "fetch_current_labels", _raise_404)

    result = undo_service.undo_run(workbook, "run-1")

    assert result.status == "done"
    assert result.outcomes[0].outcome == "not_found"
    assert fake_client.modify_calls == []
    # A message that's genuinely gone still lets the run be marked handled.
    assert workbook.system_runs.latest_undoable() is None
