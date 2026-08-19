"""Gmail change detection via the history feed (Phase 13, CLAUDE.md §13)."""

from __future__ import annotations

from googleapiclient.errors import HttpError

from app.scheduling import history as history_mod


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "error"


class FakeGmailHistoryClient:
    """Enough of GmailReadClient for the history scan: get_profile + list_history."""

    def __init__(self, profile: dict, pages: list[dict] | None = None, error: Exception | None = None) -> None:
        self._profile = profile
        self._pages = list(pages or [])
        self._error = error
        self.calls: list[tuple[str, tuple[str, ...] | None, str | None]] = []

    def get_profile(self) -> dict:
        return self._profile

    def list_history(self, start_history_id, history_types=None, page_token=None):
        self.calls.append((start_history_id, tuple(history_types or ()), page_token))
        if self._error is not None:
            raise self._error
        return self._pages.pop(0)


def _added(message_id: str, thread_id: str) -> dict:
    return {"messagesAdded": [{"message": {"id": message_id, "threadId": thread_id}}]}


def test_current_history_id_reads_the_profile() -> None:
    gmail = FakeGmailHistoryClient(profile={"historyId": "999"})
    assert history_mod.current_history_id(gmail) == "999"


def test_scan_with_no_changes_returns_empty_and_advances_cursor() -> None:
    gmail = FakeGmailHistoryClient(
        profile={"historyId": "999"},
        pages=[{"history": [], "historyId": "150"}],
    )
    scan = history_mod.scan_for_changes(gmail, "100")
    assert scan.messages == ()
    assert scan.new_history_id == "150"
    assert scan.history_gap is False


def test_scan_collects_new_messages_and_only_asks_for_messageAdded() -> None:
    gmail = FakeGmailHistoryClient(
        profile={"historyId": "999"},
        pages=[
            {
                "history": [_added("m1", "t1"), _added("m2", "t2")],
                "historyId": "160",
            }
        ],
    )
    scan = history_mod.scan_for_changes(gmail, "100")

    assert {m.message_id for m in scan.messages} == {"m1", "m2"}
    assert gmail.calls[0][1] == ("messageAdded",)


def test_scan_dedupes_the_same_message_seen_twice() -> None:
    gmail = FakeGmailHistoryClient(
        profile={"historyId": "999"},
        pages=[
            {
                "history": [_added("m1", "t1"), _added("m1", "t1")],
                "historyId": "160",
            }
        ],
    )
    scan = history_mod.scan_for_changes(gmail, "100")
    assert len(scan.messages) == 1


def test_scan_follows_pagination_across_multiple_pages() -> None:
    gmail = FakeGmailHistoryClient(
        profile={"historyId": "999"},
        pages=[
            {"history": [_added("m1", "t1")], "historyId": "150", "nextPageToken": "p2"},
            {"history": [_added("m2", "t2")], "historyId": "160"},
        ],
    )
    scan = history_mod.scan_for_changes(gmail, "100")

    assert {m.message_id for m in scan.messages} == {"m1", "m2"}
    assert scan.new_history_id == "160"
    assert gmail.calls[1][2] == "p2"  # second call used the page token


def test_scan_resets_the_cursor_on_an_expired_history_id() -> None:
    gmail = FakeGmailHistoryClient(
        profile={"historyId": "999"},
        error=HttpError(_FakeResp(404), b"not found"),
    )
    scan = history_mod.scan_for_changes(gmail, "way-too-old")

    assert scan.messages == ()
    assert scan.history_gap is True
    # Reset to the mailbox's current history id, not left stale.
    assert scan.new_history_id == "999"


def test_scan_reraises_a_non_404_http_error() -> None:
    import pytest

    # 403 is not transient, so call_with_retry re-raises on the first
    # attempt — this test would otherwise burn real wall-clock time on
    # retry backoff for a transient status like 500.
    gmail = FakeGmailHistoryClient(
        profile={"historyId": "999"},
        error=HttpError(_FakeResp(403), b"forbidden"),
    )
    with pytest.raises(HttpError):
        history_mod.scan_for_changes(gmail, "100")
