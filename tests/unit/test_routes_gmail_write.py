"""Real Gmail write routes: the manual batch /gmail/apply endpoint and
cosmetic label color sync.

The one property every test here ultimately protects: nothing calls a real
Gmail write unless DRY_RUN=false and GMAIL_PROCESSING_ENABLED=true.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from tests.fixtures.emails import DEFAULT_USER, gmail_message


class FakeGmailClient:
    """Read client stand-in — list + a per-id get_message for label lookups."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self._by_id = {m["id"]: m for m in messages}

    def get_profile(self) -> dict:
        return {"emailAddress": DEFAULT_USER}

    def list_recent_messages(self, max_results: int = 10, query: str | None = None):
        return self._messages[:max_results]

    def get_message(self, message_id: str, message_format: str = "full"):
        return self._by_id[message_id]


class FakeWriteClient:
    """Write client stand-in — records every call, mutates a shared label map."""

    def __init__(self, labels_by_id: dict[str, list[str]]) -> None:
        self._labels_by_id = labels_by_id
        self.modify_calls: list[tuple[str, list[str], list[str]]] = []
        self.ensured_labels: list[str] = []
        self.synced_colors = False

    def label_names(self) -> set[str]:
        return set()

    def ensure_labels(self, names: list[str]) -> dict[str, str]:
        self.ensured_labels.extend(names)
        return {name: name for name in names}

    def sync_label_colors(self) -> dict[str, str]:
        self.synced_colors = True
        from app.gmail.write_client import LABEL_COLORS

        return {name: "colored" for name in LABEL_COLORS}

    def modify_message(self, message_id, *, add_label_ids=None, remove_label_ids=None):
        self.modify_calls.append((message_id, list(add_label_ids or []), list(remove_label_ids or [])))
        current = set(self._labels_by_id.get(message_id, []))
        current |= set(add_label_ids or [])
        current -= set(remove_label_ids or [])
        self._labels_by_id[message_id] = sorted(current)
        return {"id": message_id, "labelIds": self._labels_by_id[message_id]}


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
            labels=["INBOX"],
        ),
        gmail_message(
            message_id="reviewed",
            headers={
                "From": "Deals <deals@shop.example>",
                "To": DEFAULT_USER,
                "Subject": "50% off sale!",
            },
            plain_body="Huge limited-time sale.",
            labels=["Review"],
        ),
    ]


@pytest.fixture
def gmail_write_wired(monkeypatch: pytest.MonkeyPatch):
    """A fake read client and a fake write client, with an empty rules file."""
    save_token(
        StoredToken(
            refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER
        )
    )
    messages = _messages()
    read_client = FakeGmailClient(messages)
    labels_by_id = {m["id"]: list(m["labelIds"]) for m in messages}
    write_client = FakeWriteClient(labels_by_id)

    monkeypatch.setattr("app.gmail.client.get_client", lambda: read_client)
    monkeypatch.setattr("app.gmail.write_client.get_write_client", lambda: write_client)
    monkeypatch.setattr(
        "app.gmail.people.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("contacts unavailable")),
    )
    return write_client


def _open_write_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    get_settings.cache_clear()


# --------------------------------------------------------------------
# Manual batch apply
# --------------------------------------------------------------------


def test_apply_previews_by_default_even_with_gate_open(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    """confirm defaults to false — always preview first."""
    write_client = gmail_write_wired
    _open_write_gate(monkeypatch)
    resp = client.post("/gmail/apply", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["wrote_to_gmail"] is False
    assert write_client.modify_calls == []


def test_apply_confirm_true_is_still_refused_when_gate_closed(
    client: TestClient, gmail_write_wired
) -> None:
    write_client = gmail_write_wired
    resp = client.post("/gmail/apply", params={"limit": 2, "confirm": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["wrote_to_gmail"] is False
    assert body["gate_allowed"] is False
    assert body["gate_reasons"]
    assert write_client.modify_calls == []


def test_apply_confirm_true_writes_for_real_when_gate_open(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_client = gmail_write_wired
    _open_write_gate(monkeypatch)

    resp = client.post(
        "/gmail/apply", params={"limit": 2, "confirm": "true", "use_ai": "false"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["wrote_to_gmail"] is True
    assert body["total"] == 2

    # The bank alert (financial + action) is protected and stays put or is
    # labeled — either way it's a real Gmail call, not a dry preview.
    assert len(write_client.modify_calls) >= 1


def test_apply_never_calls_trash(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The automated apply path has no Trash call to make — it only ever
    touches labels/INBOX/IMPORTANT (FakeWriteClient doesn't even define one)."""
    _open_write_gate(monkeypatch)
    resp = client.post("/gmail/apply", params={"limit": 2, "confirm": "true"})
    assert resp.status_code == 200


# --------------------------------------------------------------------
# Label color sync -- cosmetic, deliberately not behind the write gate
# --------------------------------------------------------------------


def test_sync_colors_works_even_with_write_gate_closed(
    client: TestClient, gmail_write_wired
) -> None:
    """Coloring existing labels changes no content/placement, so it isn't
    gated by DRY_RUN/GMAIL_PROCESSING_ENABLED."""
    write_client = gmail_write_wired
    resp = client.post("/gmail/labels/sync-colors")
    assert resp.status_code == 200
    body = resp.json()
    assert write_client.synced_colors is True
    assert body["colored"] > 0
    assert body["not_created_yet"] == 0


def test_sync_colors_reports_a_clear_error_when_the_scope_is_missing(
    client: TestClient, gmail_write_wired
) -> None:
    """A token that hasn't actually been granted gmail.labels yet (e.g. the
    Render redeploy-seed case, which optimistically records every currently
    active scope without a real re-consent) gets a clear 409, not a raw 500."""
    from googleapiclient.errors import HttpError

    class _FakeResp:
        status = 403
        reason = "insufficient scope"

    write_client = gmail_write_wired

    def _raise_insufficient_scope():
        raise HttpError(_FakeResp(), b"insufficient authentication scopes")

    write_client.sync_label_colors = _raise_insufficient_scope

    resp = client.post("/gmail/labels/sync-colors")
    assert resp.status_code == 409
    assert "oauth/start" in resp.json()["detail"]
