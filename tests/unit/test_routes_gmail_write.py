"""Real Gmail write routes (Phase 11): Restore to Inbox, Trash (with its
confirm page), and the manual batch /gmail/apply endpoint.

The one property every test here ultimately protects: nothing calls a real
Gmail write unless DRY_RUN=false, GMAIL_PROCESSING_ENABLED=true, and the
workbook says the acceptance run passed — all three, every time.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dashboard import auth
from app.gmail.tokens import StoredToken, save_token
from app.oauth_scopes import ACTIVE_SCOPES
from app.sheets.repository import ControlWorkbook
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import DEFAULT_USER, gmail_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


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
        self.trash_calls: list[str] = []
        self.ensured_labels: list[str] = []
        self.synced_colors = False

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

    def trash_message(self, message_id):
        self.trash_calls.append(message_id)
        current = set(self._labels_by_id.get(message_id, []))
        current.discard("INBOX")
        current.add("TRASH")
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
            # Already archived out of the Inbox, as a real Review-routed
            # message would be — this is what makes Restore meaningful.
            labels=["AI/Review"],
        ),
    ]


@pytest.fixture
def gmail_write_wired(monkeypatch: pytest.MonkeyPatch):
    """A real fake workbook, a fake read client, and a fake write client."""
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

    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    wb = ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: wb),
    )
    return wb, write_client


def _open_write_gate(monkeypatch: pytest.MonkeyPatch, workbook: ControlWorkbook) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    get_settings.cache_clear()
    workbook.settings.set("last_acceptance_passed", "true")


def _sign_in(client: TestClient) -> None:
    client.cookies.set(auth.SESSION_COOKIE, auth.issue_session(DEFAULT_USER))


def _action_form(**overrides: str) -> dict[str, str]:
    fields = {
        "message_id": "reviewed",
        "thread_id": "reviewed",
        "sender_email": "deals@shop.example",
        "sender_name": "Deals",
        "subject": "50% off sale!",
        "classification": "AI/Review",
        "reason": "user requested",
    }
    fields.update(overrides)
    return fields


# --------------------------------------------------------------------
# Restore to Inbox
# --------------------------------------------------------------------


def test_restore_refuses_when_write_gate_is_closed(
    client: TestClient, gmail_write_wired
) -> None:
    workbook, write_client = gmail_write_wired
    _sign_in(client)
    resp = client.post(
        "/dashboard/action/restore",
        data=_action_form(message_id="p1", thread_id="p1"),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?error=")
    assert write_client.modify_calls == []
    assert workbook.audit_log.all() == []


def test_restore_moves_message_back_to_inbox_when_gate_open(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook, write_client = gmail_write_wired
    _open_write_gate(monkeypatch, workbook)
    _sign_in(client)

    resp = client.post(
        "/dashboard/action/restore",
        data=_action_form(),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?notice=")
    assert write_client.modify_calls == [("reviewed", ["INBOX"], [])]

    rows = workbook.audit_log.all()
    assert len(rows) == 1
    assert rows[0].get("inbox_before") == "false"
    assert rows[0].get("inbox_after") == "true"
    assert rows[0].get("reversible") == "true"


def test_restore_is_a_no_op_when_already_in_inbox(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook, write_client = gmail_write_wired
    _open_write_gate(monkeypatch, workbook)
    _sign_in(client)

    resp = client.post(
        "/dashboard/action/restore",
        data=_action_form(message_id="p1", thread_id="p1"),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?notice=")
    assert write_client.modify_calls == []  # already had INBOX — nothing to do


# --------------------------------------------------------------------
# Trash confirmation page + action
# --------------------------------------------------------------------


def test_trash_confirm_requires_sign_in(client: TestClient) -> None:
    resp = client.get("/dashboard/trash-confirm?message_id=x", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


def test_trash_confirm_requires_message_id(client: TestClient, gmail_write_wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard/trash-confirm")
    assert resp.status_code == 400


def test_trash_confirm_page_names_the_message_and_does_not_trash_it(
    client: TestClient, gmail_write_wired
) -> None:
    workbook, write_client = gmail_write_wired
    _sign_in(client)
    resp = client.get(
        "/dashboard/trash-confirm",
        params={
            "message_id": "reviewed",
            "thread_id": "reviewed",
            "sender_email": "deals@shop.example",
            "sender_name": "Deals",
            "subject": "50% off sale!",
        },
    )
    assert resp.status_code == 200
    assert "50% off sale!" in resp.text
    assert "recoverable" in resp.text.lower()
    assert 'action="/dashboard/action/trash"' in resp.text
    # A GET here must never itself trash anything.
    assert write_client.trash_calls == []
    assert workbook.audit_log.all() == []


def test_trash_refuses_when_write_gate_is_closed(
    client: TestClient, gmail_write_wired
) -> None:
    workbook, write_client = gmail_write_wired
    _sign_in(client)
    resp = client.post(
        "/dashboard/action/trash", data=_action_form(), follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?error=")
    assert write_client.trash_calls == []


def test_trash_moves_to_gmail_trash_when_confirmed_and_gate_open(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook, write_client = gmail_write_wired
    _open_write_gate(monkeypatch, workbook)
    _sign_in(client)

    resp = client.post(
        "/dashboard/action/trash", data=_action_form(), follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?notice=")
    assert write_client.trash_calls == ["reviewed"]

    rows = workbook.audit_log.all()
    assert len(rows) == 1
    assert "recoverable" in rows[0].get("action_taken", "").lower()
    assert rows[0].get("reversible") == "true"


# --------------------------------------------------------------------
# Manual batch apply
# --------------------------------------------------------------------


def test_apply_previews_by_default_even_with_gate_open(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    """confirm defaults to false — same shape as /acceptance/run's preview-first."""
    workbook, write_client = gmail_write_wired
    _open_write_gate(monkeypatch, workbook)
    resp = client.post("/gmail/apply", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["wrote_to_gmail"] is False
    assert write_client.modify_calls == []
    assert workbook.audit_log.all() == []


def test_apply_confirm_true_is_still_refused_when_gate_closed(
    client: TestClient, gmail_write_wired
) -> None:
    workbook, write_client = gmail_write_wired
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
    workbook, write_client = gmail_write_wired
    _open_write_gate(monkeypatch, workbook)

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
    audited = workbook.audit_log.all()
    assert len(audited) == body["changed_count"]
    for row in audited:
        assert row.get("reversible") == "true"


def test_apply_never_calls_trash(
    client: TestClient, gmail_write_wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The automated apply path only ever touches labels/INBOX/IMPORTANT."""
    workbook, write_client = gmail_write_wired
    _open_write_gate(monkeypatch, workbook)
    client.post("/gmail/apply", params={"limit": 2, "confirm": "true"})
    assert write_client.trash_calls == []


# --------------------------------------------------------------------
# Label color sync -- cosmetic, deliberately not behind the write gate
# --------------------------------------------------------------------


def test_sync_colors_works_even_with_write_gate_closed(
    client: TestClient, gmail_write_wired
) -> None:
    """Coloring existing labels changes no content/placement, so it isn't
    gated by DRY_RUN/GMAIL_PROCESSING_ENABLED/the acceptance run."""
    _, write_client = gmail_write_wired
    resp = client.post("/gmail/labels/sync-colors")
    assert resp.status_code == 200
    body = resp.json()
    assert write_client.synced_colors is True
    assert body["colored"] > 0
    assert body["not_created_yet"] == 0
