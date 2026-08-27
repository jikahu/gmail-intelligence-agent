"""Precedence order (CLAUDE.md §11) and the safety invariants (§15, §21).

The single most important test in the project is
:func:`test_no_protected_email_is_ever_routed_to_review`. CLAUDE.md §15 makes
"zero protected emails wrongly routed to Review" the gate that decides whether
live write mode may ever be switched on.
"""

from __future__ import annotations

import pytest

from app.classification.context import ClassificationContext, build_rule, build_vendor_rule
from app.classification.engine import Classification, classify
from app.classification.labels import (
    LABEL_POLICIES,
    Label,
    Priority,
    combine_policies,
    gmail_labels,
    most_urgent,
)
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
# Label policy arithmetic
# --------------------------------------------------------------------


def test_every_label_has_a_policy() -> None:
    for label in Label:
        assert label in LABEL_POLICIES, label


def test_keeping_visible_beats_archiving() -> None:
    """CLAUDE.md §21: a cleaner inbox never beats not hiding something."""
    combined = combine_policies({Label.PURCHASES_RECEIPTS, Label.ACTION_REQUIRED})

    assert combined.keep_in_inbox
    assert not combined.archive


def test_archive_survives_when_nothing_wants_visibility() -> None:
    combined = combine_policies({Label.PURCHASES_RECEIPTS, Label.LOW_VALUE})

    assert combined.archive
    assert not combined.keep_in_inbox


def test_important_flag_propagates() -> None:
    assert combine_policies({Label.CRITICAL, Label.LOW_VALUE}).mark_important


def test_internal_labels_are_stripped_before_gmail() -> None:
    names = gmail_labels({Label.TRASH_CANDIDATE, Label.REVIEW})

    assert names == ["Review"]
    assert "Trash-Candidate" not in names


def test_priority_ordering() -> None:
    assert most_urgent(Priority.P3_NORMAL, Priority.P1_URGENT) is Priority.P1_URGENT
    assert most_urgent(Priority.P3_NORMAL, Priority.P2_IMPORTANT) is Priority.P2_IMPORTANT
    assert Priority.P1_URGENT.rank < Priority.P2_IMPORTANT.rank


# --------------------------------------------------------------------
# Precedence (CLAUDE.md §11 steps 2-6)
# --------------------------------------------------------------------


def test_manual_rule_outranks_deterministic_classification(context) -> None:
    """Step 2 beats step 6."""
    rule = build_rule(
        "alerts@chase.com", "classify_as", action="AI/Personal", scope="sender"
    )
    context.sender_rules[rule.target] = rule

    result = classify(
        make_message(sender="alerts@chase.com", subject="Your account statement"),
        context,
    )

    assert result.has(Label.PERSONAL)
    assert not result.has(Label.FINANCIAL)


def test_vendor_rule_subject_contains_swaps_out_financial(context) -> None:
    """A config-defined vendor rule (CLAUDE.md §11) replaces categorization
    the same way classify_as sender/domain rules already do."""
    rule = build_vendor_rule(match="subject_contains", value="equity", label="Equity")
    context.vendor_rules = (rule,)

    result = classify(
        make_message(
            sender="statements@equitybank.co.ke",
            subject="Your Equity account statement",
        ),
        context,
    )

    assert result.forced_vendor_label == "Equity"
    assert not result.has(Label.FINANCIAL)


def test_vendor_rule_sender_contains_matches_domain(context) -> None:
    rule = build_vendor_rule(match="sender_contains", value="arvocap", label="Arvocap")
    context.vendor_rules = (rule,)

    result = classify(
        make_message(
            sender="statements@arvocap.co.ke",
            subject="Your monthly portfolio statement",
        ),
        context,
    )

    assert result.forced_vendor_label == "Arvocap"
    assert not result.has(Label.FINANCIAL)


def test_vendor_rule_sender_contains_matches_display_name(context) -> None:
    rule = build_vendor_rule(match="sender_contains", value="arvocap", label="Arvocap")
    context.vendor_rules = (rule,)

    result = classify(
        make_message(
            sender="noreply@genericmailer.com",
            sender_name="Arvocap Asset Managers",
            subject="Your monthly portfolio statement",
        ),
        context,
    )

    assert result.forced_vendor_label == "Arvocap"
    assert not result.has(Label.FINANCIAL)


def test_vendor_rule_does_not_match_unrelated_mail(context) -> None:
    rule = build_vendor_rule(match="subject_contains", value="equity", label="Equity")
    context.vendor_rules = (rule,)

    result = classify(
        make_message(sender="statements@mybank.com", subject="Your monthly statement"),
        context,
    )

    assert result.forced_vendor_label is None
    assert result.has(Label.FINANCIAL)


def test_existing_sender_rule_outranks_a_vendor_rule(context) -> None:
    """An explicit, exact sender-level classify_as rule wins over a
    substring-based vendor rule for the same message."""
    sender_rule = build_rule(
        "statements@equitybank.co.ke", "classify_as", action="Personal", scope="sender"
    )
    context.sender_rules[sender_rule.target] = sender_rule
    context.vendor_rules = (
        build_vendor_rule(match="subject_contains", value="equity", label="Equity"),
    )

    result = classify(
        make_message(
            sender="statements@equitybank.co.ke",
            subject="Your Equity account statement",
        ),
        context,
    )

    assert result.has(Label.PERSONAL)
    assert result.forced_vendor_label is None


def test_manual_blacklist_outranks_relationship(context) -> None:
    """An explicit user decision beats an inferred relationship."""
    rule = build_rule("friend@example.com", "blacklist", scope="sender")
    context.sender_rules[rule.target] = rule

    result = classify(make_message(sender="friend@example.com", subject="hello"), context)

    assert result.review
    assert not result.protected


def test_protection_outranks_the_generic_review_rules(context) -> None:
    """CLAUDE.md §9: "protected rules outrank generic Review rules"."""
    result = classify(
        make_message(
            sender="billing@utility.com",
            subject="Your bill is ready — save 10% by paying early",
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.protected
    assert not result.review
    assert any("vetoed by protection" in reason for reason in result.rules_triggered)


def test_bulk_does_not_override_substack(context) -> None:
    """CLAUDE.md §9 lists Substack as immune to the bulk signal."""
    result = classify(
        make_message(
            sender="writer@goodwriter.substack.com",
            subject="The weekly essay",
            headers={**bulk_headers(), **substack_headers()},
        ),
        context,
    )

    assert not result.review
    assert result.archive
    assert not result.keep_in_inbox


def test_security_outranks_relationship(context) -> None:
    """CLAUDE.md §7: "Security may override relationship protection"."""
    context.known_contacts.add("compromised@evil.xyz")
    result = classify(
        make_message(
            sender="compromised@evil.xyz",
            sender_name="IT Helpdesk helpdesk@realcorp.com",
            subject="Verify your account or it will be closed",
            body="Click here to verify your account. Your account will be suspended.",
        ),
        context,
    )

    assert result.has(Label.SUSPICIOUS)
    assert result.review


def test_starring_a_message_protects_it(context) -> None:
    """Behavioural signal (step 7): the user marked this as mattering."""
    result = classify(
        make_message(
            sender="promo@vendor.com",
            subject="Massive sale this weekend",
            labels=["INBOX", "STARRED"],
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.protected
    assert not result.review


# --------------------------------------------------------------------
# The launch gate (CLAUDE.md §15)
# --------------------------------------------------------------------


def _protected_corpus() -> list[tuple[str, object]]:
    """A stratified sample of email that must never land in Review."""
    return [
        ("bank statement", make_message(
            sender="alerts@chase.com", subject="Your account statement is ready")),
        ("payment due", make_message(
            sender="billing@utility.com", subject="Your bill is due on 30 August")),
        ("payment failed", make_message(
            sender="billing@saas.com", subject="Your payment failed")),
        ("investment", make_message(
            sender="statements@broker.com", subject="Your portfolio statement")),
        ("tax", make_message(
            sender="noreply@irs.gov", subject="Your tax return has been received")),
        ("government", make_message(
            sender="noreply@immigration.gov", subject="Your visa application update")),
        ("legal", make_message(
            sender="counsel@lawfirm.com", subject="Legal notice regarding your contract")),
        ("insurance", make_message(
            sender="service@insurer.com", subject="Your policy renewal notice")),
        ("medical", make_message(
            sender="portal@clinic.com", subject="Your lab results are available")),
        ("appointment", make_message(
            sender="reception@dentist.com", subject="Appointment reminder for Tuesday")),
        ("receipt", make_message(
            sender="orders@store.com", subject="Your order #9912 is confirmed")),
        ("delivery", make_message(
            sender="ship@courier.com", subject="Your package is out for delivery")),
        ("flight", make_message(
            sender="noreply@airline.com", subject="Your flight itinerary for Nairobi")),
        ("hotel", make_message(
            sender="res@hotel.com", subject="Hotel booking confirmation")),
        ("calendar", make_message(
            sender="calendar-notification@google.com",
            subject="Invitation: Q3 planning meeting")),
        ("security", make_message(
            sender="no-reply@accounts.google.com",
            subject="Security alert: new sign-in")),
        ("education", make_message(
            sender="no-reply@learn.example", subject="Module 4 lecture is available")),
        ("certificate", make_message(
            sender="no-reply@learn.example",
            subject="Your certificate of completion", attachments=[pdf("cert.pdf")])),
        ("career", make_message(
            sender="recruiter@corp.com", subject="Interview invitation")),
        ("attachment", make_message(
            sender="unknown@somewhere.com", subject="Documents",
            attachments=[pdf("contract.pdf")])),
        ("substack", make_message(
            sender="writer@goodwriter.substack.com", subject="The weekly essay",
            headers=substack_headers())),
        ("known contact", make_message(
            sender="friend@example.com", subject="are you free saturday?")),
        ("prior correspondent", make_message(
            sender="colleague@work.com", subject="following up on that")),
        ("vip", make_message(sender="boss@work.com", subject="quick note")),
        ("active thread", make_message(
            sender="vendor@supplier.com", subject="Re: our order",
            thread_message_count=6, user_in_thread=True)),
        ("starred", make_message(
            sender="anyone@anywhere.com", subject="Buy now, huge sale",
            labels=["INBOX", "STARRED"], headers=bulk_headers())),
    ]


def _protected_corpus_with_bulk_headers() -> list[tuple[str, object]]:
    """The same corpus, but every message also looks like a mass mailing.

    Real receipts, statements and booking confirmations almost always carry
    list headers. If bulk detection could override protection, this is where
    it would show up.
    """
    out = []
    for name, message in _protected_corpus():
        message.headers.update({k.lower(): v for k, v in bulk_headers().items()})
        out.append((f"{name} (bulk headers)", message))
    return out


@pytest.mark.parametrize(
    ("name", "message"),
    _protected_corpus() + _protected_corpus_with_bulk_headers(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_no_protected_email_is_ever_routed_to_review(name, message, context) -> None:
    """The CLAUDE.md §15 launch gate: this rate must be exactly zero."""
    result = classify(message, context)

    assert not result.review, (
        f"{name!r} was wrongly routed to Review: {result.review_reason}"
    )


def test_the_gate_measures_a_real_rate(context) -> None:
    """Sanity check: the corpus is actually being protected, not just quiet."""
    results = [classify(message, context) for _name, message in _protected_corpus()]

    assert all(r.protected for r in results)
    assert sum(r.review for r in results) == 0


def test_review_candidates_still_get_reviewed(context) -> None:
    """The gate must not be satisfiable by simply never reviewing anything."""
    junk = [
        make_message(sender="promo@a.com", subject="50% off sale", headers=bulk_headers()),
        make_message(sender="rep@b.com", subject="Quick question", headers=bulk_headers()),
        make_message(
            sender="news@c.com", subject="We miss you", headers=bulk_headers()
        ),
        make_message(
            sender="x@d.com", subject="Crypto airdrop, guaranteed returns",
            headers=bulk_headers()),
    ]
    results = [classify(m, context) for m in junk]

    assert all(r.review for r in results)
    assert all(r.archive and not r.keep_in_inbox for r in results)


# --------------------------------------------------------------------
# Invariants enforced in code
# --------------------------------------------------------------------


def test_engine_refuses_to_emit_a_contradictory_decision() -> None:
    """The engine self-checks; a violation raises rather than passing silently."""
    from app.classification.engine import _assert_safety_invariants
    from app.classification.signals import Signals

    bad = Classification(
        message_id="x", protected=True, review=True, keep_in_inbox=False
    )
    with pytest.raises(AssertionError, match="protected email"):
        _assert_safety_invariants(bad, Signals())

    contradictory = Classification(message_id="x", review=True, keep_in_inbox=True)
    with pytest.raises(AssertionError, match="cannot also stay"):
        _assert_safety_invariants(contradictory, Signals())


def test_a_reviewed_message_is_archived_never_deleted(context) -> None:
    result = classify(
        make_message(sender="promo@x.com", subject="sale", headers=bulk_headers()),
        context,
    )

    assert result.review
    assert result.archive
    assert not result.keep_in_inbox


def test_important_priorities_are_never_routed_to_review(context) -> None:
    """If the engine calls it P1 or P2, it must not then hide it."""
    result = classify(
        make_message(
            sender="billing@saas.com",
            subject="Important changes to your pricing",
            headers=bulk_headers(),
        ),
        context,
    )

    assert result.priority is Priority.P2_IMPORTANT
    assert not result.review
    assert any("vetoed by P2" in reason for reason in result.rules_triggered)


def test_the_priority_veto_is_enforced_in_code() -> None:
    from app.classification.engine import _assert_safety_invariants
    from app.classification.signals import Signals

    bad = Classification(
        message_id="x",
        priority=Priority.P2_IMPORTANT,
        review=True,
        keep_in_inbox=False,
    )
    with pytest.raises(AssertionError, match="P2 email was routed to Review"):
        _assert_safety_invariants(bad, Signals())


def test_suspicious_mail_is_reviewed_even_at_higher_priority(context) -> None:
    """The security carve-out must survive both vetoes."""
    result = classify(
        make_message(
            sender="alerts@paypa1.xyz",
            subject="Your payment failed — verify your account immediately",
            body="Your account will be suspended. Click here to verify.",
        ),
        context,
    )

    assert result.has(Label.SUSPICIOUS)
    assert result.review


def test_newsletter_label_is_not_stuck_on_everything_with_list_headers(context) -> None:
    """Statements and receipts carry List-Id too — they aren't newsletters."""
    statement = classify(
        make_message(
            sender="alerts@chase.com",
            subject="Your account statement is ready",
            headers=bulk_headers(),
        ),
        context,
    )
    receipt = classify(
        make_message(
            sender="orders@store.com",
            subject="Your order #9912 is confirmed",
            headers=bulk_headers(),
        ),
        context,
    )

    assert not statement.has(Label.NEWSLETTER)
    assert statement.has(Label.FINANCIAL)
    assert not receipt.has(Label.NEWSLETTER)
    assert receipt.has(Label.PURCHASES_RECEIPTS)


def test_substack_is_always_labelled_a_newsletter(context) -> None:
    result = classify(
        make_message(
            sender="writer@goodwriter.substack.com",
            subject="The weekly essay on money and markets",
            headers=substack_headers(),
        ),
        context,
    )

    assert result.has(Label.NEWSLETTER)


def test_p1_is_never_archived(context) -> None:
    for subject in (
        "URGENT: your flight has been cancelled",
        "Your payment failed",
        "Security alert: new sign-in detected",
    ):
        result = classify(
            make_message(sender="noreply@service.com", subject=subject, headers=bulk_headers()),
            context,
        )
        assert result.priority is Priority.P1_URGENT, subject
        assert result.keep_in_inbox, subject
        assert not result.archive, subject
