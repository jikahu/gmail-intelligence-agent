"""The real-time poll cycle (Phase 13, CLAUDE.md §13) — thread-aware
classification of genuinely new mail, applied only when the write gate
allows it, idempotent, and never crashing the whole cycle on one bad
message or thread.

The rules engine itself is monkeypatched out (``app.scheduling.poller.classify``)
so these tests exercise the poller's own orchestration — gate checks,
thread fetch, apply, audit rows, idempotency, error isolation — without
depending on the deterministic engine's exact real-world behavior, which
already has its own exhaustive test suite.
"""

from __future__ import annotations

import pytest

from app.classification.engine import Classification
from app.classification.labels import Label
from app.gmail import apply as gmail_apply
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from app.scheduling import poller as poller_mod
from tests.fixtures.emails import gmail_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService

USER_EMAIL = "jikahu@gmail.com"


# --------------------------------------------------------------------
# Fixtures / fakes
# --------------------------------------------------------------------


@pytest.fixture
def workbook() -> ControlWorkbook:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    return ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)


@pytest.fixture(autouse=True)
def _no_contacts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contacts lookup degrades gracefully — never reach the real People API."""
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable in tests")),
    )


def _open_gate(monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    get_settings.cache_clear()
    workbook.settings.set("last_acceptance_passed", "true")


class FakeGmailClient:
    """Enough of GmailReadClient for the poller: profile, history, threads."""

    def __init__(self, history_id: str = "100") -> None:
        self.history_id = history_id
        self.history_pages: list[dict] = []
        self.threads: dict[str, dict] = {}
        self.thread_calls: list[str] = []

    def get_profile(self) -> dict:
        return {"emailAddress": USER_EMAIL, "historyId": self.history_id}

    def list_history(self, start_history_id, history_types=None, page_token=None):
        return self.history_pages.pop(0)

    def get_thread_full(self, thread_id: str) -> dict:
        self.thread_calls.append(thread_id)
        return self.threads[thread_id]


class FakeWriteClient:
    def __init__(self, initial_labels: dict[str, list[str]] | None = None) -> None:
        self._labels = {k: set(v) for k, v in (initial_labels or {}).items()}
        self.modify_calls: list[tuple[str, list[str], list[str]]] = []

    def ensure_labels(self, names: list[str]) -> dict[str, str]:
        return {name: name for name in names}

    def modify_message(self, message_id, *, add_label_ids=None, remove_label_ids=None):
        self.modify_calls.append(
            (message_id, list(add_label_ids or []), list(remove_label_ids or []))
        )
        current = self._labels.setdefault(message_id, set())
        current |= set(add_label_ids or [])
        current -= set(remove_label_ids or [])
        return {"id": message_id, "labelIds": sorted(current)}


def _added(message_id: str, thread_id: str) -> dict:
    return {"messagesAdded": [{"message": {"id": message_id, "threadId": thread_id}}]}


def _history_page(records: list[dict], history_id: str) -> dict:
    return {"history": records, "historyId": history_id}


def _thread(*messages: dict) -> dict:
    return {"messages": list(messages)}


def _new_mail(message_id: str = "m1", thread_id: str = "t1", labels=None) -> dict:
    return gmail_message(
        message_id=message_id,
        thread_id=thread_id,
        headers={
            "From": "someone@example.com",
            "To": USER_EMAIL,
            "Subject": "Hello",
        },
        plain_body="Hi there.",
        labels=labels if labels is not None else ["INBOX", "UNREAD"],
    )


def _stub_classify(monkeypatch: pytest.MonkeyPatch, decision: Classification) -> None:
    monkeypatch.setattr(poller_mod, "classify", lambda message, context: decision)


# --------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------


def test_first_poll_bootstraps_without_processing_anything(
    workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail = FakeGmailClient(history_id="500")
    monkeypatch.setattr("app.gmail.client.get_client", lambda: gmail)

    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.bootstrapped is True
    assert report.messages_processed == 0
    assert workbook.settings.get(poller_mod.HISTORY_CURSOR_KEY) == "500"


# --------------------------------------------------------------------
# Shared setup for "second poll onward" tests
# --------------------------------------------------------------------


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook) -> FakeGmailClient:
    client = FakeGmailClient(history_id="999")
    monkeypatch.setattr("app.gmail.client.get_client", lambda: client)
    # Pretend a prior poll already happened and recorded a cursor.
    workbook.settings.set(poller_mod.HISTORY_CURSOR_KEY, "100")
    return client


def test_no_changes_reports_nothing_and_advances_cursor(gmail, workbook) -> None:
    gmail.history_pages = [_history_page([], "150")]
    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.messages_seen == 0
    assert workbook.settings.get(poller_mod.HISTORY_CURSOR_KEY) == "150"


def test_history_gap_is_reported_and_cursor_resets(gmail, workbook) -> None:
    from googleapiclient.errors import HttpError

    class _FakeResp:
        status = 404
        reason = "gone"

    def _raise(*_a, **_k):
        raise HttpError(_FakeResp(), b"not found")

    gmail.list_history = _raise  # type: ignore[assignment]
    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.history_gap is True
    assert workbook.settings.get(poller_mod.HISTORY_CURSOR_KEY) == "999"


# --------------------------------------------------------------------
# Gate closed: dry, proposal-only logging
# --------------------------------------------------------------------


def test_gate_closed_classifies_and_logs_a_proposal_without_writing(
    gmail, workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail.history_pages = [_history_page([_added("m1", "t1")], "150")]
    gmail.threads["t1"] = _thread(_new_mail("m1", "t1"))

    decision = Classification(
        labels={Label.PERSONAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    _stub_classify(monkeypatch, decision)

    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.gate_allowed is False
    assert report.messages_processed == 1
    assert report.changed_count == 0
    assert "proposed only" in report.processed[0].action_taken

    rows = workbook.audit_log.all()
    assert len(rows) == 1
    assert rows[0].get("labels_before") == rows[0].get("labels_after")
    assert workbook.system_runs.all() == []  # nothing to undo when nothing was written


# --------------------------------------------------------------------
# Gate open: real writes
# --------------------------------------------------------------------


def test_gate_open_applies_the_change_and_records_an_undoable_run(
    gmail, workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_gate(monkeypatch, workbook)
    gmail.history_pages = [_history_page([_added("m1", "t1")], "150")]
    gmail.threads["t1"] = _thread(_new_mail("m1", "t1", labels=["INBOX"]))

    write_client = FakeWriteClient(initial_labels={"m1": ["INBOX"]})
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: write_client)

    decision = Classification(
        labels={Label.FINANCIAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    _stub_classify(monkeypatch, decision)

    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.gate_allowed is True
    assert report.changed_count == 1
    assert write_client.modify_calls  # a real modify call happened

    run_row = workbook.system_runs.latest_undoable()
    assert run_row is not None
    assert run_row.get("mode") == "real_time"

    rows = workbook.audit_log.all()
    assert len(rows) == 1
    assert rows[0].get("reversible") == "true"


def test_reprocessing_the_same_already_correct_message_is_a_no_op(
    gmail, workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotency: once a message already carries its target labels, a
    second sighting of it (an overlapping history page, a retried cycle)
    must not write to Gmail again or log another audit row."""
    _open_gate(monkeypatch, workbook)
    write_client = FakeWriteClient(initial_labels={"m1": ["INBOX", "AI/Financial"]})
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: write_client)

    decision = Classification(
        labels={Label.FINANCIAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    _stub_classify(monkeypatch, decision)

    gmail.history_pages = [_history_page([_added("m1", "t1")], "150")]
    gmail.threads["t1"] = _thread(_new_mail("m1", "t1", labels=["INBOX", "AI/Financial"]))

    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.changed_count == 0
    assert write_client.modify_calls == []
    assert workbook.audit_log.all() == []
    # A System_Runs row is still written when the gate is open (matching
    # write_service.apply_recent's own precedent), but nothing changed, so
    # it is never offered to Undo Last Run.
    assert workbook.system_runs.latest_undoable() is None


# --------------------------------------------------------------------
# Skips
# --------------------------------------------------------------------


def test_the_users_own_outgoing_message_is_skipped(
    gmail, workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_gate(monkeypatch, workbook)
    write_client = FakeWriteClient()
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: write_client)

    sent = gmail_message(
        message_id="m1",
        thread_id="t1",
        headers={"From": USER_EMAIL, "To": "someone@example.com", "Subject": "Re: hi"},
        plain_body="Sure, sounds good.",
        labels=["SENT"],
    )
    gmail.history_pages = [_history_page([_added("m1", "t1")], "150")]
    gmail.threads["t1"] = _thread(sent)

    called = {"n": 0}

    def _fail_if_called(message, context):
        called["n"] += 1
        raise AssertionError("classify should not run on the user's own sent mail")

    monkeypatch.setattr(poller_mod, "classify", _fail_if_called)

    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert called["n"] == 0
    assert report.processed[0].action_taken == "skipped — sent by the user"
    assert write_client.modify_calls == []


def test_a_message_no_longer_in_the_thread_is_skipped_without_crashing(
    gmail, workbook
) -> None:
    gmail.history_pages = [_history_page([_added("ghost", "t1")], "150")]
    gmail.threads["t1"] = _thread()  # empty — the message vanished

    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.messages_processed == 1
    assert report.error_count == 0
    assert "no longer in the thread" in report.processed[0].action_taken


# --------------------------------------------------------------------
# Error isolation
# --------------------------------------------------------------------


def test_a_failed_thread_fetch_does_not_stop_the_rest_of_the_cycle(
    gmail, workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail.history_pages = [
        _history_page([_added("bad", "t-bad"), _added("good", "t-good")], "150")
    ]
    gmail.threads["t-good"] = _thread(_new_mail("good", "t-good"))
    # t-bad deliberately missing from gmail.threads -> KeyError inside the fetch.

    decision = Classification(
        labels={Label.PERSONAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    _stub_classify(monkeypatch, decision)

    report = poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert report.error_count == 1
    outcomes = {p.message_id: p for p in report.processed}
    assert outcomes["bad"].error
    assert outcomes["good"].error == ""
    assert outcomes["good"].action_taken  # the good thread still got processed


def test_thread_fetch_happens_once_per_thread_even_with_two_new_messages(
    gmail, workbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail.history_pages = [
        _history_page([_added("m1", "t1"), _added("m2", "t1")], "150")
    ]
    gmail.threads["t1"] = _thread(
        _new_mail("m1", "t1"),
        gmail_message(
            message_id="m2",
            thread_id="t1",
            headers={"From": "other@example.com", "To": USER_EMAIL, "Subject": "Re: Hello"},
            plain_body="Following up.",
            labels=["INBOX"],
        ),
    )
    decision = Classification(labels=set(), keep_in_inbox=True, archive=False, mark_important=False)
    _stub_classify(monkeypatch, decision)

    poller_mod.run_poll_cycle(workbook=workbook, use_ai=False)

    assert gmail.thread_calls == ["t1"]
