"""Real-time processing routes: GET /realtime/status and POST /realtime/poll.

Deep coverage of the poll cycle itself (thread fetch, idempotency, error
isolation, the write gate) lives in test_scheduling_poller.py — these tests
only check the HTTP wiring: NotConnectedError -> 409, the response shape,
and that /realtime/poll actually calls the one shared implementation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.emails import DEFAULT_USER, gmail_message


class FakeGmailClient:
    def __init__(self, history_id: str = "999") -> None:
        self.history_id = history_id
        self.history_pages: list[dict] = []
        self.threads: dict[str, dict] = {}

    def get_profile(self) -> dict:
        return {"emailAddress": DEFAULT_USER, "historyId": self.history_id}

    def list_history(self, start_history_id, history_types=None, page_token=None):
        return self.history_pages.pop(0)

    def get_thread_full(self, thread_id: str) -> dict:
        return self.threads[thread_id]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """A connected token plus a fake Gmail client."""
    from app.gmail.tokens import StoredToken, save_token
    from app.oauth_scopes import ACTIVE_SCOPES

    save_token(StoredToken(refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER))

    gmail = FakeGmailClient()
    monkeypatch.setattr("app.gmail.client.get_client", lambda: gmail)
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    return gmail


# --------------------------------------------------------------------
# /realtime/status
# --------------------------------------------------------------------


def test_status_reports_nothing_polled_yet_by_default(client: TestClient) -> None:
    resp = client.get("/realtime/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["poll_count"] == 0
    assert body["last_run_at"] is None
    assert body["last_result"] is None


# --------------------------------------------------------------------
# /realtime/poll
# --------------------------------------------------------------------


def test_poll_refuses_without_a_connected_account(client: TestClient) -> None:
    resp = client.post("/realtime/poll")
    assert resp.status_code == 409


def test_poll_bootstraps_on_first_call(client: TestClient, wired) -> None:
    resp = client.post("/realtime/poll")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bootstrapped"] is True
    assert body["messages_processed"] == 0
    assert "starting point" in body["note"]


def test_poll_reports_nothing_new(client: TestClient, wired) -> None:
    from app.scheduling import state as state_mod

    gmail = wired
    state_mod.save_cursor("100")
    gmail.history_pages = [{"history": [], "historyId": "150"}]

    resp = client.post("/realtime/poll")
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages_seen"] == 0
    assert body["note"] == "Nothing new since the last poll."


def test_poll_reports_a_proposal_when_the_write_gate_is_closed(
    client: TestClient, wired
) -> None:
    from app.scheduling import state as state_mod

    gmail = wired
    state_mod.save_cursor("100")
    gmail.history_pages = [
        {
            "history": [{"messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]}],
            "historyId": "150",
        }
    ]
    gmail.threads["t1"] = {
        "messages": [
            gmail_message(
                message_id="m1",
                thread_id="t1",
                headers={"From": "someone@example.com", "To": DEFAULT_USER, "Subject": "Hi"},
                plain_body="Hello there.",
                labels=["INBOX"],
            )
        ]
    }

    resp = client.post("/realtime/poll?use_ai=false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gate_allowed"] is False
    assert body["changed_count"] == 0
    assert "proposal only" in body["note"]
