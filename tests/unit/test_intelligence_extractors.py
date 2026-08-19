"""Per-message extractors: deadlines, subscriptions, material changes, financial."""

from __future__ import annotations

from datetime import date

from app.intelligence import deadlines, financial, material, subscriptions
from tests.fixtures.emails import make_message

TODAY = date(2026, 8, 17)


# -------- deadlines --------


def test_payment_deadline_is_actionable() -> None:
    m = make_message(subject="Invoice", body="Payment due by September 20, 2026.")
    found = deadlines.extract_deadlines(m, TODAY, action_required=True)
    assert len(found) == 1
    assert found[0].category == "payment"
    assert found[0].action_required is True
    assert found[0].iso == "2026-09-20"
    assert found[0].status == "upcoming"


def test_a_date_with_no_cue_is_not_a_deadline() -> None:
    m = make_message(subject="Recap", body="Thanks for joining our September 15, 2026 recap.")
    assert deadlines.extract_deadlines(m, TODAY) == []


def test_overdue_status_when_date_has_passed() -> None:
    m = make_message(subject="Reminder", body="Payment due by August 1, 2026.")
    found = deadlines.extract_deadlines(m, TODAY)
    assert found[0].status == "overdue"


def test_action_required_single_date_becomes_generic_deadline() -> None:
    m = make_message(
        subject="Following up",
        body="Please take care of this. The relevant date is October 5, 2026.",
    )
    found = deadlines.extract_deadlines(m, TODAY, action_required=True)
    assert len(found) == 1
    assert found[0].category == "generic"
    assert found[0].action_required is True


# -------- subscriptions --------


def test_subscription_is_detected_with_amount_and_renewal() -> None:
    m = make_message(
        sender="billing@netflix.com",
        sender_name="Netflix",
        subject="Your subscription renews soon",
        body="Your plan is $15.99 per month and renews on September 1, 2026.",
    )
    sub = subscriptions.extract_subscription(m, TODAY)
    assert sub is not None
    assert sub.service == "Netflix"
    assert sub.amount == 15.99
    assert sub.currency == "USD"
    assert sub.billing_frequency == "monthly"
    assert sub.renewal_date == "2026-09-01"


def test_non_subscription_returns_none() -> None:
    m = make_message(subject="Lunch tomorrow?", body="Want to grab lunch?")
    assert subscriptions.extract_subscription(m, TODAY) is None


def test_trial_ending_suggests_review() -> None:
    m = make_message(
        sender="billing@app.com",
        subject="Your free trial ends soon",
        body="Your free trial ends September 5, 2026 and your subscription begins.",
    )
    sub = subscriptions.extract_subscription(m, TODAY)
    assert sub is not None
    assert sub.review_status == "suggested_review"


# -------- material changes --------


def test_price_change_with_old_and_new_values() -> None:
    m = make_message(
        subject="Important changes to your plan pricing",
        body="Your price is increasing from $9.99 to $12.99, effective November 1, 2026.",
    )
    change = material.extract_material_change(m, TODAY)
    assert change is not None
    assert change.kind == "price"
    assert change.old_value == "$9.99"
    assert change.new_value == "$12.99"
    assert change.effective_date == "2026-11-01"


def test_fee_change_is_typed_as_fee() -> None:
    m = make_message(
        subject="Important changes to your account",
        body="Your monthly maintenance fee is changing from $5 to $8, effective October 1, 2026.",
    )
    change = material.extract_material_change(m, TODAY)
    assert change is not None
    assert change.kind == "fee"
    assert change.new_value == "$8"


def test_no_material_change_returns_none() -> None:
    m = make_message(subject="Weekly digest", body="Here are your updates for the week.")
    assert material.extract_material_change(m, TODAY) is None


# -------- financial detail --------


def test_financial_extracts_amount_due_and_safe_ref() -> None:
    m = make_message(
        sender="statements@chase.com",
        subject="Your statement is ready",
        body="Your balance is $342.50. Payment due by 09/20/2026. Card ending in 4321.",
    )
    detail = financial.extract_financial(m, TODAY)
    assert detail is not None
    assert detail.kind == "statement"
    assert detail.amount == 342.50
    assert detail.currency == "USD"
    assert detail.due_date == "2026-09-20"
    assert detail.account_ref == "4321"


def test_financial_never_stores_more_than_four_account_digits() -> None:
    m = make_message(
        sender="statements@chase.com",
        subject="Statement",
        body="balance $10. Account number 1234 5678 9012 3456.",
    )
    detail = financial.extract_financial(m, TODAY)
    assert detail is not None
    assert detail.account_ref == "3456"


def test_non_financial_returns_none() -> None:
    m = make_message(subject="Hello", body="Just saying hi, hope you're well.")
    assert financial.extract_financial(m, TODAY) is None
