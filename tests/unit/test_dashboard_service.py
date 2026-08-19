"""The dashboard aggregation service (Phase 8).

Feeds the builder a small, realistic set of classified messages and checks the
Command Center it produces: card order, counts, and the row lists behind them.
The pipeline's Gmail read is stubbed; everything else (intelligence, follow-up
timers, card assembly) runs for real.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.classification.engine import classify
from app.classification.pipeline import PreviewResult
from app.dashboard import service
from tests.fixtures.emails import bulk_headers, make_message

# A Monday, so "tomorrow" is a business day and lands in Due Soon.
TODAY = date(2026, 8, 17)


def _result(message) -> PreviewResult:
    return PreviewResult(message=message, classification=classify(message))


def _sample_results() -> list[PreviewResult]:
    return [
        # P1 — a payment failure is urgent on its own.
        _result(
            make_message(
                message_id="p1",
                sender="alerts@bank.com",
                sender_name="Example Bank",
                subject="Action needed: your payment failed",
                body="Your payment failed and your card was declined. Update it.",
                snippet="Your payment failed and your card was declined. Update it to avoid...",
            )
        ),
        # A promo from a stranger → AI/Review.
        _result(
            make_message(
                message_id="promo",
                sender="deals@shop.example",
                subject="50% off everything — limited time!",
                body="Huge sale, don't miss out. Unsubscribe here.",
                headers=bulk_headers(),
            )
        ),
        # A bill due tomorrow → Due Soon deadline.
        _result(
            make_message(
                message_id="soon",
                sender="billing@utility.com",
                subject="Your bill",
                body="Payment due tomorrow. Amount due $40.",
            )
        ),
        # A bill long past due → Overdue deadline.
        _result(
            make_message(
                message_id="late",
                sender="billing@utility.com",
                subject="Overdue notice",
                body="Payment due January 1, 2020. Please pay now.",
            )
        ),
    ]


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> service.CommandCenter:
    results = _sample_results()
    monkeypatch.setattr(
        "app.classification.pipeline.preview_recent",
        lambda **kwargs: results,
    )
    # No workbook in a unit test → no VIP suggestions (degrades to empty).
    monkeypatch.setattr(
        "app.sheets.repository.ControlWorkbook.connect",
        classmethod(lambda cls, spreadsheet_id=None: (_ for _ in ()).throw(
            RuntimeError("workbook unavailable")
        )),
    )
    return service.build_command_center(today=TODAY)


def test_cards_are_in_spec_order(built: service.CommandCenter) -> None:
    keys = [card.key for card in built.cards]
    assert keys == [
        "p1", "p2", "action", "waiting", "due_soon",
        "overdue", "review", "vip", "subscriptions",
    ]


def test_card_counts_match_their_lists(built: service.CommandCenter) -> None:
    for card in built.cards:
        assert card.count == len(built.rows(card.key))


def test_expected_cards_are_populated(built: service.CommandCenter) -> None:
    assert built.card("p1").count >= 1
    assert built.card("review").count >= 1
    assert built.card("due_soon").count >= 1
    assert built.card("overdue").count >= 1
    # No workbook, so no VIP suggestions.
    assert built.card("vip").count == 0


def test_review_row_carries_display_fields(built: service.CommandCenter) -> None:
    rows = built.rows("review")
    assert rows, "expected at least one Review row"
    row = rows[0]
    assert row.subject
    assert row.reason  # why it was set aside
    assert 0.0 <= (row.confidence or 0.0) <= 1.0
    assert isinstance(row.labels, list)


def test_row_carries_the_real_email_snippet_and_sender_address(
    built: service.CommandCenter,
) -> None:
    """CLAUDE.md §13 asks for both a one-line summary of the message *and*
    a separate reason it was flagged — the snippet is the former (Gmail's
    own preview text), distinct from ``reason`` (the classifier's rationale).
    """
    row = next(r for r in built.rows("p1") if r.message_id == "p1")
    assert row.snippet == "Your payment failed and your card was declined. Update it to avoid..."
    assert row.snippet != row.reason
    assert row.sender_email == "alerts@bank.com"
    assert row.sender_name == "Example Bank"


def test_gmail_url_links_to_the_real_message(built: service.CommandCenter) -> None:
    row = next(r for r in built.rows("p1") if r.message_id == "p1")
    assert row.gmail_url == "https://mail.google.com/mail/u/0/#all/p1"


def test_gmail_url_is_empty_without_a_real_message() -> None:
    from app.sheets.repository import VIP

    row = service._row_from_vip(VIP(email="a@b.com", name="A", status="pending"))
    assert row.message_id == ""
    assert row.gmail_url == ""


def test_p1_never_appears_in_review(built: service.CommandCenter) -> None:
    # The launch-gate spirit: nothing urgent hides in Review.
    review_ids = {row.message_id for row in built.rows("review")}
    p1_ids = {row.message_id for row in built.rows("p1")}
    assert review_ids.isdisjoint(p1_ids)


def test_dry_run_flag_is_carried(built: service.CommandCenter) -> None:
    # Default settings have dry_run=True.
    assert built.dry_run is True
