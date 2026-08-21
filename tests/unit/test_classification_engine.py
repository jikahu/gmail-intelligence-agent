"""End-to-end behaviour of the deterministic rules engine."""

from __future__ import annotations

import pytest

from app.classification.context import ClassificationContext, build_rule
from app.classification.engine import (
    CONFIDENCE_EXPLICIT_RULE,
    CONFIDENCE_FALLBACK,
    CONFIDENCE_UNRESOLVED,
    classify,
    classify_all,
)
from app.classification.labels import Label, Priority
from tests.fixtures.emails import (
    DEFAULT_USER,
    bulk_headers,
    make_message,
    pdf,
    substack_headers,
)


@pytest.fixture
def context() -> ClassificationContext:
    return ClassificationContext(
        user_email=DEFAULT_USER,
        known_contacts={"friend@example.com"},
        prior_correspondents={"colleague@work.com"},
        vip_emails={"boss@work.com"},
    )


# --------------------------------------------------------------------
# Financial and security (CLAUDE.md §7)
# --------------------------------------------------------------------


def test_bank_statement_is_financial_and_archives_not_reviewed(context) -> None:
    """Financial alone (no Critical/Action-Required/Security) still archives
    once labeled — CLAUDE.md §7.1 — but must never be routed to Review."""
    result = classify(
        make_message(sender="alerts@chase.com", subject="Your account statement is ready"),
        context,
    )

    assert result.has(Label.FINANCIAL)
    assert result.priority is Priority.P2_IMPORTANT
    assert result.archive
    assert not result.keep_in_inbox
    assert not result.review
    assert result.protected


def test_payment_declined_is_p1_and_action_required(context) -> None:
    result = classify(
        make_message(sender="billing@service.com", subject="Your payment failed"),
        context,
    )

    assert result.priority is Priority.P1_URGENT
    assert result.has(Label.ACTION_REQUIRED)
    assert result.keep_in_inbox
    assert result.mark_important


def test_fraud_alert_combines_critical_financial_security_action(context) -> None:
    """The combination spelled out in CLAUDE.md §7."""
    result = classify(
        make_message(
            sender="alerts@bank.com",
            subject="Fraud alert: unauthorized transaction on your card",
        ),
        context,
    )

    assert result.has(Label.CRITICAL)
    assert result.has(Label.FINANCIAL)
    assert result.has(Label.SECURITY)
    assert result.has(Label.ACTION_REQUIRED)
    assert result.priority is Priority.P1_URGENT


def test_security_alert_is_critical_and_important(context) -> None:
    result = classify(
        make_message(
            sender="no-reply@accounts.google.com",
            subject="Security alert: new sign-in on a new device",
        ),
        context,
    )

    assert result.has(Label.SECURITY)
    assert result.has(Label.CRITICAL)
    assert result.priority is Priority.P1_URGENT
    assert result.keep_in_inbox
    assert result.mark_important


def test_money_mention_alone_is_not_critical(context) -> None:
    """CLAUDE.md §7: "Money mention alone ≠ Critical"."""
    result = classify(
        make_message(sender="friend@example.com", subject="I'll pay you back the $20"),
        context,
    )

    assert not result.has(Label.CRITICAL)
    assert result.priority is not Priority.P1_URGENT


# --------------------------------------------------------------------
# Purchases, deliveries, travel
# --------------------------------------------------------------------


def test_receipt_is_archived_but_protected(context) -> None:
    """Protection isn't the same as staying in the Inbox (§8)."""
    result = classify(
        make_message(sender="orders@store.com", subject="Your order #4432 is confirmed"),
        context,
    )

    assert result.has(Label.PURCHASES_RECEIPTS)
    assert result.archive
    assert not result.keep_in_inbox
    assert result.protected
    assert not result.review


def test_delivery_problem_keeps_the_message_in_the_inbox(context) -> None:
    result = classify(
        make_message(sender="ship@store.com", subject="Delivery delayed — action needed"),
        context,
    )

    assert result.has(Label.ACTION_REQUIRED)
    assert result.keep_in_inbox


def test_cancelled_flight_is_p1_and_never_archived(context) -> None:
    """A travel booking normally archives — urgency must override that."""
    result = classify(
        make_message(
            sender="noreply@airline.com",
            subject="URGENT: your flight has been cancelled",
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.priority is Priority.P1_URGENT
    assert result.keep_in_inbox
    assert not result.archive


# --------------------------------------------------------------------
# Newsletters (CLAUDE.md §9)
# --------------------------------------------------------------------


def test_substack_archives_not_reviewed(context) -> None:
    """Substack is never sent to Review, but — like every other category —
    still archives once labeled (CLAUDE.md §7.1)."""
    result = classify(
        make_message(
            sender="writer@goodwriter.substack.com",
            subject="This week in technology",
            headers=substack_headers(),
        ),
        context,
    )

    assert result.has(Label.NEWSLETTER)
    assert not result.review
    assert result.archive
    assert not result.keep_in_inbox


def test_other_newsletters_default_to_review(context) -> None:
    result = classify(
        make_message(
            sender="news@somesite.com",
            subject="Your weekly roundup",
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.has(Label.NEWSLETTER)
    assert result.review
    assert result.archive
    assert "haven't approved" in (result.review_reason or "")


def test_an_approved_newsletter_sender_is_kept(context) -> None:
    rule = build_rule("news@somesite.com", "whitelist", scope="sender")
    context.sender_rules[rule.target] = rule

    result = classify(
        make_message(
            sender="news@somesite.com",
            subject="Your weekly roundup",
            headers=bulk_headers(),
        ),
        context,
    )

    assert not result.review
    assert result.protected


# --------------------------------------------------------------------
# Review candidates (CLAUDE.md §9)
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "FLASH SALE — 50% off everything",
        "Your exclusive coupon inside",
        "We miss you! Come back for a special offer",
        "Quick question about your workflow",
        "Free webinar: register now to save your seat",
        "Take our quick survey",
    ],
)
def test_promotional_and_cold_mail_goes_to_review(subject: str, context) -> None:
    result = classify(
        make_message(sender="marketing@vendor.com", subject=subject, headers=bulk_headers()),
        context,
    )

    assert result.review, subject
    assert result.archive
    assert result.review_reason


def test_review_never_implies_deletion(context) -> None:
    result = classify(
        make_message(sender="promo@x.com", subject="Huge sale", headers=bulk_headers()),
        context,
    )

    assert result.review
    assert result.archive
    # There is no delete/trash concept on a Classification at all.
    assert not hasattr(result, "trash")
    assert not hasattr(result, "delete")


def test_social_notifications_go_to_review(context) -> None:
    result = classify(
        make_message(
            sender="notify@social.example",
            subject="You have 3 new connection requests",
            labels=["INBOX", "CATEGORY_SOCIAL"],
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.review


def test_trash_candidate_never_reaches_gmail() -> None:
    """The label is an internal concept only (CLAUDE.md §6)."""
    result = classify(make_message(subject="anything"))
    result.labels.add(Label.TRASH_CANDIDATE)

    assert Label.TRASH_CANDIDATE.value not in result.gmail_label_names


# --------------------------------------------------------------------
# Relationship and VIP
# --------------------------------------------------------------------


def test_known_contact_is_personal_and_archives(context) -> None:
    result = classify(
        make_message(sender="friend@example.com", subject="dinner on saturday?"), context
    )

    assert result.has(Label.PERSONAL)
    assert result.archive
    assert not result.keep_in_inbox
    assert result.protected


def test_personal_mail_is_not_marked_important_by_default(context) -> None:
    """CLAUDE.md §7: don't auto-mark Personal as Important without cause."""
    result = classify(
        make_message(sender="friend@example.com", subject="dinner on saturday?"), context
    )

    assert result.has(Label.PERSONAL)
    assert not result.mark_important


def test_personal_mail_with_an_action_is_marked_important(context) -> None:
    result = classify(
        make_message(
            sender="friend@example.com", subject="Please confirm you can make it"
        ),
        context,
    )

    assert result.has(Label.ACTION_REQUIRED)
    assert result.mark_important


def test_friend_mentioning_a_sale_is_still_personal(context) -> None:
    """Promotional *wording* must not outweigh an actual relationship."""
    result = classify(
        make_message(sender="friend@example.com", subject="check out this sale I found"),
        context,
    )

    assert result.has(Label.PERSONAL)
    assert not result.review


def test_vip_engagement_bait_archives_not_reviewed(context) -> None:
    result = classify(
        make_message(sender="boss@work.com", subject="we miss you at standup"), context
    )

    assert not result.review
    assert result.archive
    assert not result.keep_in_inbox


def test_prior_correspondent_is_protected(context) -> None:
    result = classify(
        make_message(sender="colleague@work.com", subject="following up"), context
    )

    assert result.protected
    assert not result.review


def test_active_thread_is_protected(context) -> None:
    result = classify(
        make_message(
            sender="stranger@vendor.com",
            subject="Re: our conversation",
            thread_message_count=5,
            user_in_thread=True,
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.protected
    assert not result.review


# --------------------------------------------------------------------
# Suspicious mail
# --------------------------------------------------------------------


def test_phishing_is_flagged_and_reviewed(context) -> None:
    result = classify(
        make_message(
            sender="security@paypa1.xyz",
            subject="Verify your account or it will be suspended",
            body="Click here to verify your account immediately.",
        ),
        context,
    )

    assert result.has(Label.SUSPICIOUS)
    assert result.has(Label.REVIEW)
    assert result.review
    assert result.archive


def test_phishing_from_a_known_contact_is_still_flagged(context) -> None:
    """Security may override relationship protection (CLAUDE.md §7)."""
    context.known_contacts.add("friend@evil.xyz")
    result = classify(
        make_message(
            sender="friend@evil.xyz",
            sender_name="Chase Support support@chase.com",
            subject="Verify your account or it will be closed",
            body="Click here to verify. Your account will be suspended.",
        ),
        context,
    )

    assert result.has(Label.SUSPICIOUS)
    assert result.review


# --------------------------------------------------------------------
# Education, career, documents
# --------------------------------------------------------------------


def test_genuine_education_is_archived(context) -> None:
    result = classify(
        make_message(
            sender="no-reply@learn.example",
            subject="Module 3 lecture is now available",
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.has(Label.EDUCATION)
    assert result.archive
    assert not result.review


def test_education_with_a_deadline_stays_in_the_inbox(context) -> None:
    result = classify(
        make_message(
            sender="no-reply@learn.example",
            subject="Your assignment is due tomorrow",
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.has(Label.EDUCATION)
    assert result.has(Label.ACTION_REQUIRED)
    assert result.keep_in_inbox


def test_career_mail_is_kept_and_important(context) -> None:
    result = classify(
        make_message(
            sender="recruiter@corp.com",
            subject="Interview invitation — please confirm your availability",
        ),
        context,
    )

    assert result.has(Label.CAREER)
    assert result.has(Label.ACTION_REQUIRED)
    assert result.keep_in_inbox
    assert result.mark_important


def test_tax_document_is_an_important_document(context) -> None:
    result = classify(
        make_message(
            sender="payroll@corp.com",
            subject="Your W-2 tax document",
            attachments=[pdf("w2.pdf")],
        ),
        context,
    )

    assert result.has(Label.IMPORTANT_DOCUMENT)
    assert result.has(Label.FINANCIAL)
    assert result.protected


def test_a_promotional_pdf_is_protected_but_not_an_important_document(context) -> None:
    result = classify(
        make_message(
            sender="promo@random.io",
            subject="Limited time offer inside",
            attachments=[pdf("offer.pdf")],
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.protected          # attachments always protect (§8)
    assert not result.review         # so it is never swept away
    assert not result.has(Label.IMPORTANT_DOCUMENT)  # but it isn't a record


def test_subscription_renewal_is_flagged_for_review_of_the_subscription(context) -> None:
    result = classify(
        make_message(
            sender="billing@saas.com", subject="Your subscription renews on 1 September"
        ),
        context,
    )

    assert result.has(Label.SUBSCRIPTION_REVIEW)
    # Flagging a subscription must never cancel anything.
    assert not hasattr(result, "cancel")


def test_price_increase_is_p2(context) -> None:
    result = classify(
        make_message(
            sender="billing@saas.com", subject="Important changes to your pricing"
        ),
        context,
    )

    assert result.priority is Priority.P2_IMPORTANT


# --------------------------------------------------------------------
# Explicit rules
# --------------------------------------------------------------------


def test_classify_as_rule_forces_the_label(context) -> None:
    rule = build_rule("odd@sender.com", "classify_as", action="AI/Financial", scope="sender")
    context.sender_rules[rule.target] = rule

    result = classify(make_message(sender="odd@sender.com", subject="anything"), context)

    assert result.has(Label.FINANCIAL)
    assert result.confidence == CONFIDENCE_EXPLICIT_RULE


def test_blacklist_rule_sends_mail_to_review(context) -> None:
    rule = build_rule("noise@sender.com", "blacklist", scope="sender")
    context.sender_rules[rule.target] = rule

    result = classify(make_message(sender="noise@sender.com", subject="hello"), context)

    assert result.review
    assert "rule" in (result.review_reason or "")


def test_an_unknown_rule_action_does_not_crash(context) -> None:
    rule = build_rule("odd@sender.com", "classify_as", action="AI/Nonsense", scope="sender")
    context.sender_rules[rule.target] = rule

    result = classify(make_message(sender="odd@sender.com", subject="hello"), context)

    assert result is not None  # falls back to normal classification


# --------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------


def test_sent_mail_is_not_classified_as_incoming(context) -> None:
    result = classify(
        make_message(sender=DEFAULT_USER, subject="my own message", sent_by_user=True),
        context,
    )

    assert result.sent_by_user
    assert not result.labels
    assert not result.review


def test_unresolved_mail_is_handed_to_the_ai_step(context) -> None:
    """Step 8 of §11 exists precisely for this case."""
    result = classify(
        make_message(sender="no-reply@unknown.example", subject=""), context
    )

    assert not result.labels
    assert result.needs_ai
    assert result.confidence == CONFIDENCE_UNRESOLVED
    assert not result.review


def test_an_attachment_does_not_fake_confidence(context) -> None:
    """An attached PDF protects the email but doesn't say what it is."""
    result = classify(
        make_message(sender="unknown@nowhere.example", subject="", attachments=[pdf()]),
        context,
    )

    assert result.protected
    assert not result.review
    assert result.needs_ai
    assert result.confidence <= CONFIDENCE_FALLBACK


def test_every_decision_records_why(context) -> None:
    result = classify(
        make_message(sender="alerts@chase.com", subject="Your statement is ready"),
        context,
    )

    assert result.rules_triggered
    assert result.rationale
    assert result.summary()


def test_classify_all_processes_a_batch(context) -> None:
    messages = [
        make_message(message_id="a", sender="friend@example.com", subject="hi"),
        make_message(
            message_id="b", sender="promo@x.com", subject="sale", headers=bulk_headers()
        ),
    ]
    results = classify_all(messages, context)

    assert [r.message_id for r in results] == ["a", "b"]
    assert not results[0].review
    assert results[1].review


def test_engine_works_without_any_context() -> None:
    """No workbook, no contacts — it must still be safe and not crash."""
    result = classify(make_message(sender="a@b.com", subject="Your invoice is attached"))

    assert result is not None
    assert not result.review
