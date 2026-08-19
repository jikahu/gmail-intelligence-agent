"""The Command Center routes (Phase 8).

Covers the access gate, the rendered pages, the sign-in callback, and — because
subjects come from untrusted email — that email text is HTML-escaped before it
reaches the page.
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
            snippet="Your payment failed and your card was declined. Update it now to avoid...",
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


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connect an authorized account and stub Gmail/contacts/workbook."""
    save_token(
        StoredToken(
            refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER
        )
    )
    monkeypatch.setattr("app.gmail.client.get_client", lambda: FakeGmailClient(_messages()))
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


@pytest.fixture
def wired_writable(monkeypatch: pytest.MonkeyPatch) -> ControlWorkbook:
    """Like ``wired``, but the workbook is a real fake — writes actually land.

    Needed for the Review-queue action routes: they must be able to append a
    Review_Feedback row, a rule suggestion, or a VIP suggestion and have the
    test observe it.
    """
    save_token(
        StoredToken(
            refresh_token="r", scopes=list(ACTIVE_SCOPES), account_email=DEFAULT_USER
        )
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


def _sign_in(client: TestClient) -> None:
    client.cookies.set(auth.SESSION_COOKIE, auth.issue_session(DEFAULT_USER))


# --------------------------------------------------------------------
# Access gate
# --------------------------------------------------------------------


def test_dashboard_requires_sign_in(client: TestClient) -> None:
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


def test_login_page_renders(client: TestClient) -> None:
    resp = client.get("/dashboard/login")
    assert resp.status_code == 200
    assert "Sign in with Google" in resp.text


# --------------------------------------------------------------------
# Rendered dashboard
# --------------------------------------------------------------------


def test_command_center_shows_cards(client: TestClient, wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Command Center" in resp.text
    # A few of the nine cards.
    for title in ("P1 Urgent", "Action Required", "AI Review", "Subscription Review"):
        assert title in resp.text
    # The read-only guarantee is stated on the page.
    assert "NO GMAIL CHANGES ARE BEING MADE" in resp.text


def test_review_list_escapes_untrusted_subject(client: TestClient, wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard/list/review")
    assert resp.status_code == 200
    # The promo lands in Review; its script-y subject must be neutralised.
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_p1_row_shows_sender_address_snippet_and_gmail_link(
    client: TestClient, wired
) -> None:
    """A card that names a display name alone ("Bank") but hides the real
    address, and never shows what the message actually says, isn't enough
    to act on — a user shouldn't have to open Gmail just to find that out.
    """
    _sign_in(client)
    resp = client.get("/dashboard/list/p1")
    assert resp.status_code == 200
    assert "alerts@bank.com" in resp.text  # the real address, not just a tooltip
    assert "Your payment failed and your card was declined. Update it now" in resp.text
    assert "https://mail.google.com/mail/u/0/#all/p1" in resp.text
    assert "Open in Gmail" in resp.text


def test_review_list_shows_all_seven_buttons_live(client: TestClient, wired) -> None:
    _sign_in(client)
    resp = client.get("/dashboard/list/review")
    assert "Restore to Inbox" in resp.text
    assert "Trash" in resp.text
    # Keep/Review-Correct/rule-suggestion/VIP buttons are live since Phase 9.
    assert 'action="/dashboard/action/keep"' in resp.text
    assert 'action="/dashboard/action/suggest-vip"' in resp.text
    # Restore to Inbox is a live form post too (Phase 11).
    assert 'action="/dashboard/action/restore"' in resp.text
    # Trash isn't a direct post — it's a link to a confirmation page first
    # (CLAUDE.md §5), so there must be no direct trash-action form.
    assert 'action="/dashboard/action/trash"' not in resp.text
    assert "/dashboard/trash-confirm?" in resp.text


def test_unknown_list_is_404(client: TestClient, wired) -> None:
    _sign_in(client)
    assert client.get("/dashboard/list/not-a-card").status_code == 404


# --------------------------------------------------------------------
# Sign-in callback
# --------------------------------------------------------------------


def test_callback_signs_in_authorized_account(
    client: TestClient, wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "complete_login", lambda code, state: DEFAULT_USER)
    resp = client.get(
        "/dashboard/auth/callback?code=x&state=y", follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    assert auth.SESSION_COOKIE in resp.cookies


def test_callback_rejects_unauthorized_account(
    client: TestClient, wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth, "complete_login", lambda code, state: "stranger@example.com"
    )
    resp = client.get("/dashboard/auth/callback?code=x&state=y")
    assert resp.status_code == 403
    assert "isn't authorized" in resp.text


def test_logout_clears_session(client: TestClient, wired) -> None:
    _sign_in(client)
    resp = client.post("/dashboard/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


# --------------------------------------------------------------------
# Review-queue actions (Phase 9)
# --------------------------------------------------------------------


def _action_form(**overrides: str) -> dict[str, str]:
    fields = {
        "message_id": "xss",
        "thread_id": "xss",
        "sender_email": "deals@shop.example",
        "sender_name": "Deals",
        "subject": "50% off sale!",
        "classification": "AI/Review",
        "reason": "promotional wording",
    }
    fields.update(overrides)
    return fields


def test_action_requires_sign_in(client: TestClient) -> None:
    resp = client.post(
        "/dashboard/action/keep", data=_action_form(), follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


def test_unknown_action_is_404(client: TestClient, wired_writable) -> None:
    _sign_in(client)
    resp = client.post("/dashboard/action/not-a-real-action", data=_action_form())
    assert resp.status_code == 404


def test_keep_records_feedback_and_redirects_with_a_notice(
    client: TestClient, wired_writable: ControlWorkbook
) -> None:
    _sign_in(client)
    resp = client.post(
        "/dashboard/action/keep", data=_action_form(), follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?notice=")

    rows = wired_writable.review_feedback.all()
    assert len(rows) == 1
    assert rows[0].get("user_decision") == "kept"

    followed = client.get(resp.headers["location"])
    assert 'class="ok-note"' in followed.text


def test_make_sender_rule_creates_a_pending_suggestion(
    client: TestClient, wired_writable: ControlWorkbook
) -> None:
    _sign_in(client)
    resp = client.post(
        "/dashboard/action/make-sender-rule", data=_action_form(), follow_redirects=False
    )
    assert resp.status_code == 303
    assert "notice=" in resp.headers["location"]

    pending = wired_writable.rules.pending_suggestions()
    assert len(pending) == 1
    assert pending[0].get("target") == "deals@shop.example"
    assert wired_writable.rules.get_sender_rules() == []  # never active on one click


def test_make_domain_rule_refuses_a_public_provider_with_an_error_banner(
    client: TestClient, wired_writable: ControlWorkbook
) -> None:
    _sign_in(client)
    resp = client.post(
        "/dashboard/action/make-domain-rule",
        data=_action_form(sender_email="someone@gmail.com"),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?error=")
    assert wired_writable.rules.pending_suggestions() == []

    followed = client.get(resp.headers["location"])
    assert "gmail.com" in followed.text
    assert 'class="danger"' in followed.text


def test_suggest_vip_creates_a_pending_vip(
    client: TestClient, wired_writable: ControlWorkbook
) -> None:
    _sign_in(client)
    resp = client.post(
        "/dashboard/action/suggest-vip", data=_action_form(), follow_redirects=False
    )
    assert resp.status_code == 303
    suggested = {v.email for v in wired_writable.vips.suggested()}
    assert "deals@shop.example" in suggested
    assert wired_writable.vips.approved_emails() == set()


def test_action_degrades_gracefully_when_the_workbook_is_unreachable(
    client: TestClient, wired
) -> None:
    """``wired`` (unlike ``wired_writable``) makes the workbook raise."""
    _sign_in(client)
    resp = client.post(
        "/dashboard/action/keep", data=_action_form(), follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/list/review?error=")


# --------------------------------------------------------------------
# Static assets
# --------------------------------------------------------------------


def test_dashboard_css_is_served(client: TestClient) -> None:
    resp = client.get("/static/dashboard.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]
