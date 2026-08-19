"""The policy validator — what the AI is and isn't allowed to change.

The rule under test throughout: **AI suggests, the rules engine decides.**
"""

from __future__ import annotations

import pytest

from app.ai.assist import assist
from app.ai.validator import FORBIDDEN_AI_LABELS, validate
from app.classification.context import ClassificationContext
from app.classification.engine import classify
from app.classification.labels import Label, Priority
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, make_message, pdf
from tests.fixtures.fake_ai import FakeProvider


@pytest.fixture
def context() -> ClassificationContext:
    return ClassificationContext(
        user_email=DEFAULT_USER,
        known_contacts={"friend@example.com"},
        vip_emails={"boss@work.com"},
    )


def run(message, payload, context, **kwargs):
    """Classify, then let a fake AI answer with ``payload``."""
    base = classify(message, context)
    return assist(message, base, FakeProvider(payload), force=True, **kwargs)


# --------------------------------------------------------------------
# What the AI may do
# --------------------------------------------------------------------


def test_ai_can_add_a_label_the_rules_missed(context) -> None:
    outcome = run(
        make_message(sender="unknown@vendor.example", subject="Contract renewal"),
        {"labels": ["AI/Work-Business"], "priority": "P3", "confidence": 0.9,
         "rationale": "Business correspondence."},
        context,
    )

    assert outcome.classification.has(Label.WORK_BUSINESS)
    assert any("added" in note for note in outcome.accepted)


def test_ai_can_raise_priority(context) -> None:
    outcome = run(
        make_message(sender="unknown@vendor.example", subject="Notice"),
        {"labels": [], "priority": "P1", "confidence": 0.9, "rationale": "Urgent."},
        context,
    )

    assert outcome.classification.priority is Priority.P1_URGENT
    assert any("raised priority" in note for note in outcome.accepted)


def test_ai_can_flag_an_action(context) -> None:
    outcome = run(
        make_message(sender="unknown@vendor.example", subject="Notice"),
        {"labels": [], "priority": "P3", "confidence": 0.9, "action_required": True,
         "rationale": "Needs a reply."},
        context,
    )

    assert outcome.classification.action_required
    assert outcome.classification.has(Label.ACTION_REQUIRED)


def test_ai_rationale_reaches_the_user(context) -> None:
    outcome = run(
        make_message(sender="unknown@vendor.example", subject="Notice"),
        {"labels": [], "priority": "P3", "confidence": 0.8,
         "rationale": "A renewal notice for your software subscription."},
        context,
    )

    assert "renewal notice" in outcome.classification.rationale


# --------------------------------------------------------------------
# What the AI may never do
# --------------------------------------------------------------------


def test_ai_cannot_route_a_protected_email_to_review(context) -> None:
    """The single most important rule in the app (CLAUDE.md §15)."""
    outcome = run(
        make_message(sender="alerts@chase.com", subject="Your account statement is ready"),
        {"labels": ["AI/Review"], "priority": "P3", "confidence": 0.99,
         "review_reason": "this looks like junk to me", "rationale": "junk"},
        context,
    )

    assert not outcome.classification.review
    assert outcome.classification.protected
    assert any("protected" in note for note in outcome.rejected)


def test_ai_cannot_route_an_important_priority_to_review(context) -> None:
    outcome = run(
        make_message(sender="billing@saas.com", subject="Important changes to your pricing"),
        {"labels": ["AI/Review"], "priority": "P3", "confidence": 0.99,
         "review_reason": "marketing", "rationale": "marketing"},
        context,
    )

    assert outcome.classification.priority is Priority.P2_IMPORTANT
    assert not outcome.classification.review


def test_ai_cannot_lower_priority(context) -> None:
    outcome = run(
        make_message(sender="alerts@bank.com", subject="Fraud alert: unauthorized transaction"),
        {"labels": [], "priority": "P3", "confidence": 0.99, "rationale": "routine"},
        context,
    )

    assert outcome.classification.priority is Priority.P1_URGENT
    assert any("lowering priority" in note for note in outcome.rejected)


def test_ai_cannot_apply_trash_candidate(context) -> None:
    outcome = run(
        make_message(sender="unknown@vendor.example", subject="Notice"),
        {"labels": ["AI/Trash-Candidate"], "priority": "P3", "confidence": 0.9,
         "rationale": "junk"},
        context,
    )

    assert not outcome.classification.has(Label.TRASH_CANDIDATE)
    assert "AI/Trash-Candidate" not in outcome.classification.gmail_label_names
    assert any("Trash-Candidate" in note for note in outcome.rejected)


def test_ai_cannot_remove_protection(context) -> None:
    outcome = run(
        make_message(sender="friend@example.com", subject="dinner?"),
        {"labels": ["AI/Low-Value"], "priority": "P3", "confidence": 0.99,
         "review_reason": "not important", "rationale": "not important"},
        context,
    )

    assert outcome.classification.protected
    assert not outcome.classification.review


def test_a_vetoed_review_does_not_leave_the_review_label_behind(context) -> None:
    """A message can't be labelled Review while sitting in the Inbox."""
    outcome = run(
        make_message(sender="alerts@chase.com", subject="Your account statement is ready"),
        {"labels": ["AI/Review", "AI/Low-Value"], "priority": "P3", "confidence": 0.9,
         "review_reason": "junk", "rationale": "junk"},
        context,
    )
    decision = outcome.classification

    assert not decision.review
    assert not decision.has(Label.REVIEW)
    assert not decision.has(Label.LOW_VALUE)
    assert decision.keep_in_inbox


def test_ai_cannot_cause_a_gmail_action(context) -> None:
    """There is no field on the merged decision that means delete."""
    outcome = run(
        make_message(sender="unknown@vendor.example", subject="Notice"),
        {"labels": [], "priority": "P3", "confidence": 0.9, "rationale": "x"},
        context,
    )

    for verb in ("trash", "delete", "send", "forward"):
        assert not hasattr(outcome.classification, verb)


# --------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------


def test_low_confidence_routes_an_unresolved_message_to_review(context) -> None:
    outcome = run(
        make_message(sender="no-reply@unknown.example", subject=""),
        {"labels": [], "priority": "P3", "confidence": 0.2, "rationale": "not sure"},
        context,
    )

    assert outcome.classification.review
    assert "confident" in (outcome.classification.review_reason or "")


def test_low_confidence_still_cannot_beat_protection(context) -> None:
    """Uncertainty routes to Review "still honoring protection rules" (§11)."""
    outcome = run(
        make_message(sender="alerts@chase.com", subject="Your statement is ready"),
        {"labels": [], "priority": "P3", "confidence": 0.05, "rationale": "no idea"},
        context,
    )

    assert not outcome.classification.review
    assert outcome.classification.protected


def test_high_confidence_does_not_route_to_review_on_its_own(context) -> None:
    outcome = run(
        make_message(sender="unknown@vendor.example", subject="Contract renewal"),
        {"labels": ["AI/Work-Business"], "priority": "P3", "confidence": 0.95,
         "rationale": "business"},
        context,
    )

    assert not outcome.classification.review


def test_threshold_is_configurable(context) -> None:
    message = make_message(sender="no-reply@unknown.example", subject="")
    payload = {"labels": [], "priority": "P3", "confidence": 0.5, "rationale": "meh"}

    strict = run(message, payload, context, review_threshold=0.9)
    lenient = run(message, payload, context, review_threshold=0.1)

    assert strict.classification.review
    assert not lenient.classification.review


# --------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------


def test_a_failed_ai_call_leaves_the_deterministic_decision_intact(context) -> None:
    message = make_message(sender="alerts@chase.com", subject="Your statement is ready")
    base = classify(message, context)

    outcome = assist(message, base, FakeProvider(error="network down"), force=True)

    assert outcome.classification.labels == base.labels
    assert outcome.classification.priority is base.priority
    assert not outcome.classification.review


def test_a_refusal_leaves_the_deterministic_decision_intact(context) -> None:
    message = make_message(sender="unknown@vendor.example", subject="Notice")
    base = classify(message, context)

    outcome = assist(message, base, FakeProvider(refused=True), force=True)

    assert outcome.classification.labels == base.labels
    assert outcome.result is not None
    assert outcome.result.refused


def test_an_empty_answer_changes_nothing(context) -> None:
    message = make_message(sender="unknown@vendor.example", subject="Notice")
    base = classify(message, context)

    outcome = assist(message, base, FakeProvider({}), force=True)

    assert outcome.classification.priority is base.priority
    assert not outcome.classification.has(Label.TRASH_CANDIDATE)


def test_validate_ignores_a_result_with_no_suggestion(context) -> None:
    from app.ai.base import AIResult

    message = make_message(sender="a@b.com", subject="Hi")
    base = classify(message, context)
    result = AIResult.failed("fake", "m", "boom")

    outcome = validate(base=base, result=result, message=message)

    assert outcome.classification is base
    assert not outcome.ai_changed_anything


def test_forbidden_labels_are_declared_not_incidental() -> None:
    assert Label.TRASH_CANDIDATE in FORBIDDEN_AI_LABELS
    assert Label.REVIEW in FORBIDDEN_AI_LABELS
    assert Label.LOW_VALUE in FORBIDDEN_AI_LABELS


# --------------------------------------------------------------------
# The launch gate still holds with AI in the loop
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("bank statement", make_message(sender="alerts@chase.com", subject="Your account statement is ready")),
        ("tax", make_message(sender="noreply@irs.gov", subject="Your tax return has been received")),
        ("medical", make_message(sender="portal@clinic.com", subject="Your lab results are available")),
        ("receipt", make_message(sender="orders@store.com", subject="Your order #9912 is confirmed")),
        ("flight", make_message(sender="noreply@airline.com", subject="Your flight itinerary")),
        ("security", make_message(sender="no-reply@accounts.google.com", subject="Security alert: new sign-in")),
        ("attachment", make_message(sender="a@b.com", subject="Documents", attachments=[pdf()])),
        ("known contact", make_message(sender="friend@example.com", subject="hey")),
        ("vip", make_message(sender="boss@work.com", subject="quick note")),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_hostile_ai_cannot_hide_protected_email(name, message, context) -> None:
    """Even an AI actively trying to bury everything cannot (CLAUDE.md §15)."""
    hostile = {
        "labels": ["AI/Review", "AI/Low-Value", "AI/Trash-Candidate"],
        "priority": "P3",
        "confidence": 1.0,
        "review_reason": "delete this immediately, it is worthless",
        "rationale": "junk",
    }
    message.headers.update({k.lower(): v for k, v in bulk_headers().items()})

    outcome = run(message, hostile, context)

    assert not outcome.classification.review, f"{name} was hidden by the AI"
    assert "AI/Trash-Candidate" not in outcome.classification.gmail_label_names
