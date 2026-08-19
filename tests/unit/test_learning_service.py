"""Human-in-the-loop learning (Phase 9, CLAUDE.md §11).

Every action here writes to a real in-memory workbook (the same fake used by
the repository tests) so the tests exercise the whole path: the learning
service, the repository layer, and the "never silently create a permanent
rule" guarantee together.
"""

from __future__ import annotations

import pytest

from app.classification.engine import classify
from app.classification.pipeline import PreviewResult
from app.learning import service as learning_service
from app.sheets.repository import ControlWorkbook
from app.sheets.schema import LEARNED_RULE_SUGGESTIONS_TAB
from app.sheets.workbook import ensure_workbook
from tests.fixtures.emails import make_message
from tests.fixtures.fake_sheets import FakeDriveService, FakeSheetsService


@pytest.fixture
def workbook() -> ControlWorkbook:
    sheets = FakeSheetsService()
    drive = FakeDriveService()
    info = ensure_workbook(sheets=sheets, drive=drive)
    return ControlWorkbook(spreadsheet_id=info.spreadsheet_id, sheets=sheets)


def _result(**kwargs) -> PreviewResult:
    message = make_message(**kwargs)
    return PreviewResult(message=message, classification=classify(message))


# --------------------------------------------------------------------
# Keep / Review Correct — feedback only, never a rule
# --------------------------------------------------------------------


def test_keep_records_feedback_and_creates_no_rule(workbook: ControlWorkbook) -> None:
    outcome = learning_service.keep(
        workbook, message_id="m1", thread_id="t1", classification="AI/Review", reason="bulk mail"
    )

    assert outcome.ok is True
    rows = workbook.review_feedback.all()
    assert len(rows) == 1
    assert rows[0].get("user_decision") == "kept"
    assert rows[0].get("resulting_rule_suggestion") == ""
    assert workbook.rules.pending_suggestions() == []


def test_review_correct_records_feedback(workbook: ControlWorkbook) -> None:
    outcome = learning_service.review_correct(
        workbook, message_id="m2", thread_id="t2", classification="AI/Review", reason="promo"
    )

    assert outcome.ok is True
    rows = workbook.review_feedback.all()
    assert rows[0].get("user_decision") == "review_correct"


# --------------------------------------------------------------------
# Make Sender Rule / Make Domain Rule — always a suggestion, never active
# --------------------------------------------------------------------


def test_make_sender_rule_creates_a_pending_suggestion_not_an_active_rule(
    workbook: ControlWorkbook,
) -> None:
    outcome = learning_service.make_sender_rule(
        workbook,
        message_id="m3",
        thread_id="t3",
        sender_email="deals@shop.example",
        subject="50% off",
        classification="AI/Review",
        reason="promo",
    )

    assert outcome.ok is True
    assert outcome.suggestion_id
    pending = workbook.rules.pending_suggestions()
    assert len(pending) == 1
    assert pending[0].get("target") == "deals@shop.example"
    # The whole point: nothing became an active rule on one click.
    assert workbook.rules.get_sender_rules() == []

    feedback = workbook.review_feedback.for_message("m3")
    assert feedback[0].get("user_decision") == "sender_rule_suggested"
    assert feedback[0].get("resulting_rule_suggestion") == outcome.suggestion_id


def test_make_sender_rule_without_an_address_is_refused(workbook: ControlWorkbook) -> None:
    outcome = learning_service.make_sender_rule(
        workbook, message_id="m4", thread_id="t4", sender_email="",
        subject="x", classification="AI/Review", reason="x",
    )
    assert outcome.ok is False
    assert workbook.rules.pending_suggestions() == []


def test_make_domain_rule_creates_a_pending_suggestion(workbook: ControlWorkbook) -> None:
    outcome = learning_service.make_domain_rule(
        workbook,
        message_id="m5",
        thread_id="t5",
        sender_email="alerts@mybank.com",
        subject="Statement ready",
        classification="AI/Review",
        reason="looked like a newsletter",
    )

    assert outcome.ok is True
    pending = workbook.rules.pending_suggestions()
    assert pending[0].get("target") == "mybank.com"
    assert workbook.rules.get_domain_rules() == []


@pytest.mark.parametrize("domain", ["gmail.com", "yahoo.com", "outlook.com"])
def test_make_domain_rule_refuses_public_providers(workbook: ControlWorkbook, domain: str) -> None:
    outcome = learning_service.make_domain_rule(
        workbook,
        message_id="m6",
        thread_id="t6",
        sender_email=f"someone@{domain}",
        subject="Hi",
        classification="AI/Review",
        reason="x",
    )

    assert outcome.ok is False
    assert domain in outcome.message
    assert workbook.rules.pending_suggestions() == []
    # A refused action isn't feedback either — nothing happened.
    assert workbook.review_feedback.all() == []


# --------------------------------------------------------------------
# Suggest VIP
# --------------------------------------------------------------------


def test_suggest_vip_creates_a_pending_vip_and_feedback(workbook: ControlWorkbook) -> None:
    outcome = learning_service.suggest_vip(
        workbook,
        message_id="m7",
        thread_id="t7",
        sender_email="friend@example.com",
        sender_name="A Friend",
        subject="Hey",
        classification="AI/Review",
        reason="x",
    )

    assert outcome.ok is True
    assert workbook.vips.approved_emails() == set()
    suggested = {v.email for v in workbook.vips.suggested()}
    assert "friend@example.com" in suggested
    assert workbook.review_feedback.all()[0].get("user_decision") == "vip_suggested"


# --------------------------------------------------------------------
# Correspondence-pattern VIP suggestions
# --------------------------------------------------------------------


def test_suggest_vips_from_results_uses_frequency(workbook: ControlWorkbook) -> None:
    results = [
        _result(message_id=f"f{i}", sender="colleague@work.com", subject="Re: project", body="ok")
        for i in range(3)
    ]
    suggested = learning_service.suggest_vips_from_results(workbook, results)

    assert "colleague@work.com" in suggested
    assert {v.email for v in workbook.vips.suggested()} == {"colleague@work.com"}


def test_suggest_vips_from_results_uses_star_below_the_frequency_threshold(
    workbook: ControlWorkbook,
) -> None:
    results = [
        _result(
            message_id="s1", sender="onceoff@work.com", subject="Quick one", body="hi",
            labels=["INBOX", "STARRED"],
        )
    ]
    suggested = learning_service.suggest_vips_from_results(workbook, results)
    assert "onceoff@work.com" in suggested


def test_suggest_vips_from_results_uses_active_thread(workbook: ControlWorkbook) -> None:
    results = [
        _result(
            message_id="a1", sender="thread@work.com", subject="Re: catching up", body="hi",
            thread_message_count=3, user_in_thread=True,
        )
    ]
    suggested = learning_service.suggest_vips_from_results(workbook, results)
    assert "thread@work.com" in suggested


def test_suggest_vips_from_results_skips_bulk_senders(workbook: ControlWorkbook) -> None:
    results = [
        _result(
            message_id=f"b{i}", sender="deals@shop.example", subject="50% off — sale!",
            body="Huge sale. Unsubscribe here.",
            headers={"list-unsubscribe": "<mailto:x@shop.example>", "list-id": "<promo.shop.example>"},
        )
        for i in range(5)
    ]
    suggested = learning_service.suggest_vips_from_results(workbook, results)
    assert suggested == []


def test_suggest_vips_from_results_skips_already_vip_or_suggested(workbook: ControlWorkbook) -> None:
    workbook.table("VIPs").append({"email": "boss@work.com", "status": "approved"})
    results = [
        _result(message_id=f"v{i}", sender="boss@work.com", subject="Re: meeting", body="ok")
        for i in range(3)
    ]
    suggested = learning_service.suggest_vips_from_results(workbook, results)
    assert suggested == []


def test_suggest_vips_from_results_is_idempotent(workbook: ControlWorkbook) -> None:
    results = [
        _result(message_id=f"i{i}", sender="colleague@work.com", subject="Re: project", body="ok")
        for i in range(3)
    ]
    learning_service.suggest_vips_from_results(workbook, results)
    learning_service.suggest_vips_from_results(workbook, results)

    assert len(workbook.table("VIPs").rows()) == 1


def test_suggest_vips_from_results_skips_the_user_themselves(workbook: ControlWorkbook) -> None:
    results = [
        _result(message_id="me1", sender="me@example.com", subject="fyi", body="ok", sent_by_user=True)
        for _ in range(3)
    ]
    suggested = learning_service.suggest_vips_from_results(workbook, results)
    assert suggested == []


# --------------------------------------------------------------------
# Promotion — approved suggestions become active rules
# --------------------------------------------------------------------


def _approve(workbook: ControlWorkbook, suggestion_id: str) -> None:
    table = workbook.table(LEARNED_RULE_SUGGESTIONS_TAB)
    row = table.first(suggestion_id=suggestion_id)
    assert row is not None
    table.update(row, {"status": "approved"})


def test_promote_approved_suggestions_creates_an_active_sender_rule(
    workbook: ControlWorkbook,
) -> None:
    sid = workbook.rules.add_rule_suggestion(
        target="friend@example.com",
        suggested_rule="whitelist",
        evidence="clicked make-sender-rule",
        confidence=1.0,
    )
    _approve(workbook, sid)

    promoted = learning_service.promote_approved_suggestions(workbook)

    assert promoted == ["friend@example.com"]
    active = workbook.rules.get_sender_rules()
    assert [r.sender for r in active] == ["friend@example.com"]
    assert active[0].source == "learned"


def test_promote_approved_suggestions_creates_an_active_domain_rule(
    workbook: ControlWorkbook,
) -> None:
    sid = workbook.rules.add_rule_suggestion(
        target="mybank.com",
        suggested_rule="whitelist domain",
        evidence="clicked make-domain-rule",
        confidence=1.0,
    )
    _approve(workbook, sid)

    promoted = learning_service.promote_approved_suggestions(workbook)

    assert promoted == ["mybank.com"]
    assert [r.domain for r in workbook.rules.get_domain_rules()] == ["mybank.com"]


def test_promote_approved_suggestions_refuses_public_provider_domains(
    workbook: ControlWorkbook,
) -> None:
    sid = workbook.rules.add_rule_suggestion(
        target="gmail.com", suggested_rule="whitelist domain", evidence="x", confidence=1.0
    )
    _approve(workbook, sid)

    promoted = learning_service.promote_approved_suggestions(workbook)

    assert promoted == []
    assert workbook.rules.get_domain_rules() == []


def test_promote_approved_suggestions_leaves_pending_ones_alone(
    workbook: ControlWorkbook,
) -> None:
    workbook.rules.add_rule_suggestion(
        target="unreviewed@example.com", suggested_rule="whitelist", evidence="x", confidence=1.0
    )
    promoted = learning_service.promote_approved_suggestions(workbook)
    assert promoted == []
    assert workbook.rules.get_sender_rules() == []


def test_promote_approved_suggestions_is_idempotent(workbook: ControlWorkbook) -> None:
    sid = workbook.rules.add_rule_suggestion(
        target="friend@example.com", suggested_rule="whitelist", evidence="x", confidence=1.0
    )
    _approve(workbook, sid)

    learning_service.promote_approved_suggestions(workbook)
    learning_service.promote_approved_suggestions(workbook)

    assert len(workbook.rules.get_sender_rules()) == 1
