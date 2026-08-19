"""Protection rules (CLAUDE.md §8) — what may never be swept into Review."""

from __future__ import annotations

import pytest

from app.classification.context import ClassificationContext, build_rule
from app.classification.protection import evaluate
from app.classification.signals import detect
from tests.fixtures.emails import bulk_headers, make_message, pdf, substack_headers


def protect(message, context: ClassificationContext | None = None):
    context = context or ClassificationContext()
    return evaluate(message, detect(message), context)


# --------------------------------------------------------------------
# Hard-protected topics
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "topic"),
    [
        ("Your account statement is ready", "financial"),
        ("Payment due on your credit card", "financial"),
        ("Your tax return has been filed", "financial"),
        ("Legal notice regarding your contract", "legal_government"),
        ("Your visa application update", "legal_government"),
        ("Appointment reminder with Dr. Smith", "medical"),
        ("Your lab results are available", "medical"),
        ("Your insurance policy renewal notice", "insurance"),
        ("Your flight itinerary for Nairobi", "travel"),
        ("Hotel booking confirmation", "travel"),
        ("Your order confirmation #4432", "purchase"),
        ("Your package has shipped", "delivery"),
        ("Meeting invitation: Q3 planning", "calendar"),
        ("Interview invitation for the engineer role", "career"),
    ],
)
def test_hard_protected_topics_are_detected(subject: str, topic: str) -> None:
    result = protect(make_message(subject=subject))

    assert result.protected, subject
    assert topic in result.topics, f"{subject!r} should register as {topic}"


def test_security_content_is_protected_from_the_full_body() -> None:
    """Security is matched against the whole message, not just the subject."""
    result = protect(
        make_message(subject="Notice", body="We detected a new sign-in from a new device.")
    )

    assert result.protected
    assert "security" in result.topics


def test_broad_topics_only_read_the_headline() -> None:
    """A billing mention in a footer must not protect an advert."""
    result = protect(
        make_message(
            subject="Huge summer sale",
            body="Shop now! Questions about billing? Contact support. Unsubscribe here.",
            headers=bulk_headers(),
        )
    )

    assert "financial" not in result.topics


def test_attachments_always_protect() -> None:
    result = protect(make_message(subject="Anything at all", attachments=[pdf()]))

    assert result.protected
    assert "attachment" in result.topics
    assert any("attachment" in reason for reason in result.reasons)


def test_genuine_education_is_protected() -> None:
    result = protect(make_message(subject="Your assignment for module 3"))

    assert result.protected
    assert "education" in result.topics


def test_marketing_dressed_as_education_gets_no_protection() -> None:
    """CLAUDE.md §7: marketing-as-education gets no protection."""
    result = protect(
        make_message(
            subject="Free masterclass — 50% off our course today only",
            headers=bulk_headers(),
        )
    )

    assert "education" not in result.topics


# --------------------------------------------------------------------
# Relationship protection
# --------------------------------------------------------------------


def test_google_contact_is_protected() -> None:
    context = ClassificationContext(known_contacts={"friend@example.com"})
    result = protect(make_message(sender="friend@example.com", subject="hi"), context)

    assert result.protected
    assert result.is_known_contact
    assert result.relationship_only


def test_prior_correspondent_is_protected() -> None:
    context = ClassificationContext(prior_correspondents={"colleague@work.com"})
    result = protect(make_message(sender="colleague@work.com"), context)

    assert result.protected
    assert result.is_prior_correspondent


def test_active_thread_is_protected() -> None:
    message = make_message(thread_message_count=4, user_in_thread=True)
    result = protect(message)

    assert result.protected
    assert result.is_active_thread


def test_a_thread_the_user_never_joined_is_not_protected_by_that_alone() -> None:
    message = make_message(thread_message_count=4, user_in_thread=False)
    assert not protect(message).is_active_thread


def test_vip_is_protected() -> None:
    context = ClassificationContext(vip_emails={"boss@work.com"})
    result = protect(make_message(sender="boss@work.com"), context)

    assert result.protected
    assert result.is_vip


def test_substack_is_protected_from_the_generic_bulk_rule() -> None:
    result = protect(
        make_message(sender="writer@goodwriter.substack.com", headers=substack_headers())
    )

    assert result.protected
    assert any("Substack" in reason for reason in result.reasons)


# --------------------------------------------------------------------
# Manual rules
# --------------------------------------------------------------------


def test_sender_whitelist_protects() -> None:
    rule = build_rule("news@example.com", "whitelist", scope="sender")
    context = ClassificationContext(sender_rules={rule.target: rule})
    result = protect(make_message(sender="news@example.com"), context)

    assert result.protected
    assert result.matched_rule is rule


def test_domain_whitelist_covers_subdomains() -> None:
    rule = build_rule("chase.com", "whitelist", scope="domain")
    context = ClassificationContext(domain_rules={rule.target: rule})
    result = protect(make_message(sender="alerts@secure.chase.com"), context)

    assert result.protected
    assert result.matched_rule is rule


def test_blacklist_rule_removes_protection() -> None:
    """If the user says "always review this", relationship doesn't override it."""
    rule = build_rule("spammer@example.com", "blacklist", scope="sender")
    context = ClassificationContext(
        sender_rules={rule.target: rule},
        known_contacts={"spammer@example.com"},
    )
    result = protect(make_message(sender="spammer@example.com"), context)

    assert not result.protected


def test_sender_rule_beats_domain_rule() -> None:
    sender_rule = build_rule("vip@corp.com", "whitelist", scope="sender")
    domain_rule = build_rule("corp.com", "blacklist", scope="domain")
    context = ClassificationContext(
        sender_rules={sender_rule.target: sender_rule},
        domain_rules={domain_rule.target: domain_rule},
    )
    result = protect(make_message(sender="vip@corp.com"), context)

    assert result.matched_rule is sender_rule
    assert result.protected


# --------------------------------------------------------------------
# Rule construction safety
# --------------------------------------------------------------------


def test_domain_rule_on_a_public_provider_is_refused() -> None:
    """Approving one gmail.com address must never trust all of Gmail (§8)."""
    assert build_rule("gmail.com", "whitelist", scope="domain") is None
    assert build_rule("yahoo.com", "whitelist", scope="domain") is None
    assert build_rule("outlook.com", "blacklist", scope="domain") is None


def test_sender_rule_on_a_public_provider_is_fine() -> None:
    rule = build_rule("aunt@gmail.com", "whitelist", scope="sender")

    assert rule is not None
    assert rule.target == "aunt@gmail.com"


def test_rules_are_normalized() -> None:
    rule = build_rule("  @Example.COM ", "WhiteList", scope="domain")

    assert rule is not None
    assert rule.target == "example.com"
    assert rule.rule_type == "whitelist"


def test_unknown_rule_type_is_refused() -> None:
    assert build_rule("a@b.com", "explode", scope="sender") is None
    assert build_rule("", "whitelist", scope="sender") is None


# --------------------------------------------------------------------
# Security override
# --------------------------------------------------------------------


def test_phishing_overrides_relationship_protection() -> None:
    """A convincing phish from a "known" address must still be catchable (§7)."""
    context = ClassificationContext(known_contacts={"friend@evil.xyz"})
    message = make_message(
        sender="friend@evil.xyz",
        sender_name="Your Bank support@chase.com",
        subject="Verify your account or it will be suspended",
        body="Click here to verify your account immediately.",
    )
    result = evaluate(message, detect(message), context)

    assert result.security_override
    assert not result.protected


def test_phishing_does_not_override_topic_protection() -> None:
    """A real bank alert stays protected even if it trips suspicion signals."""
    message = make_message(
        sender="alerts@chase.com",
        reply_to="noreply@chase-mail.com",
        subject="Your account statement is ready",
        body="Please verify your account details to continue.",
    )
    result = evaluate(message, detect(message), ClassificationContext())

    assert result.protected
    assert not result.security_override
