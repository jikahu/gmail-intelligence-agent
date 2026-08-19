"""Structural signal detection."""

from __future__ import annotations

from app.classification.patterns import PUBLIC_EMAIL_PROVIDERS, PatternSet
from app.classification.signals import SUSPICION_THRESHOLD, detect
from tests.fixtures.emails import bulk_headers, make_message, substack_headers


# --------------------------------------------------------------------
# PatternSet
# --------------------------------------------------------------------


def test_pattern_set_matches_on_word_boundaries() -> None:
    patterns = PatternSet("test", ("bill", "payment due"))

    assert patterns.matches("your bill is ready")
    assert patterns.matches("Payment Due tomorrow")
    # Must not fire inside a longer word.
    assert not patterns.matches("billboard advertising")
    assert not patterns.matches("billing")


def test_pattern_set_reports_the_longest_match_first() -> None:
    patterns = PatternSet("test", ("payment", "payment failed"))
    assert patterns.first_match("your payment failed today") == "payment failed"


def test_pattern_set_handles_empty_text() -> None:
    patterns = PatternSet("test", ("bill",))
    assert patterns.first_match("") is None
    assert patterns.all_matches("") == []


def test_pattern_set_deduplicates_all_matches() -> None:
    patterns = PatternSet("test", ("sale",))
    assert patterns.all_matches("SALE sale Sale") == ["sale"]


# --------------------------------------------------------------------
# Bulk detection
# --------------------------------------------------------------------


def test_list_headers_mark_a_message_as_bulk() -> None:
    signals = detect(make_message(headers=bulk_headers()))

    assert signals.is_bulk
    assert signals.has_list_headers
    assert signals.has_unsubscribe
    assert any("List-Unsubscribe" in reason for reason in signals.bulk_reasons)


def test_ordinary_mail_is_not_bulk() -> None:
    signals = detect(make_message(sender="colleague@work.com", subject="Lunch?"))

    assert not signals.is_bulk
    assert not signals.has_list_headers


def test_missing_recipients_alone_does_not_mean_bulk() -> None:
    """A weak signal must not stand on its own — it only corroborates."""
    signals = detect(make_message(to=[], subject="Hello"))

    assert not signals.is_bulk
    assert signals.bulk_reasons == ()


def test_missing_recipients_corroborates_other_bulk_signals() -> None:
    signals = detect(make_message(to=[], headers=bulk_headers()))

    assert signals.is_bulk
    assert "no visible recipient" in signals.bulk_reasons


def test_precedence_bulk_header() -> None:
    assert detect(make_message(headers={"precedence": "bulk"})).is_bulk
    assert detect(make_message(headers={"precedence": "list"})).is_bulk
    assert not detect(make_message(headers={"precedence": "normal"})).is_bulk


def test_auto_submitted_header() -> None:
    assert detect(make_message(headers={"auto-submitted": "auto-generated"})).is_bulk
    assert not detect(make_message(headers={"auto-submitted": "no"})).is_bulk


def test_esp_headers_are_recognised() -> None:
    for header in ("x-campaign-id", "x-mailchimp-id", "feedback-id", "x-sg-eid"):
        assert detect(make_message(headers={header: "abc"})).is_bulk, header


# --------------------------------------------------------------------
# Newsletter and Substack
# --------------------------------------------------------------------


def test_substack_detected_from_sender_domain() -> None:
    signals = detect(make_message(sender="writer@goodwriter.substack.com"))
    assert signals.is_substack


def test_substack_detected_from_list_headers_on_a_custom_domain() -> None:
    signals = detect(
        make_message(sender="hello@customdomain.com", headers=substack_headers())
    )

    assert signals.is_substack
    assert signals.is_newsletter


def test_regular_newsletter_is_not_substack() -> None:
    signals = detect(make_message(sender="news@othersite.com", headers=bulk_headers()))

    assert signals.is_newsletter
    assert not signals.is_substack


def test_promotional_blast_is_not_treated_as_a_newsletter() -> None:
    signals = detect(
        make_message(subject="FLASH SALE 50% off everything", headers=bulk_headers())
    )

    assert signals.is_promotional
    assert not signals.is_newsletter


def test_gmail_promotions_category_is_a_promotional_signal() -> None:
    signals = detect(make_message(labels=["INBOX", "CATEGORY_PROMOTIONS"]))
    assert signals.is_promotional


def test_gmail_social_category() -> None:
    signals = detect(make_message(labels=["INBOX", "CATEGORY_SOCIAL"]))
    assert signals.is_social_notification


# --------------------------------------------------------------------
# Automated senders
# --------------------------------------------------------------------


def test_robot_local_parts_are_recognised() -> None:
    for local in ("no-reply", "noreply", "notifications", "mailer-daemon"):
        signals = detect(make_message(sender=f"{local}@service.com"))
        assert signals.is_automated_sender, local


def test_a_person_is_not_an_automated_sender() -> None:
    assert not detect(make_message(sender="alice@service.com")).is_automated_sender


# --------------------------------------------------------------------
# Suspicion scoring
# --------------------------------------------------------------------


def test_clean_mail_scores_zero_suspicion() -> None:
    signals = detect(
        make_message(sender="alerts@chase.com", subject="Your statement is ready")
    )

    assert signals.suspicion_score == 0
    assert not signals.is_suspicious


def test_phishing_wording_plus_bad_tld_trips_the_threshold() -> None:
    signals = detect(
        make_message(
            sender="security@paypa1.xyz",
            subject="Verify your account immediately",
            body="Your account will be suspended. Click here to verify.",
        )
    )

    assert signals.suspicion_score >= SUSPICION_THRESHOLD
    assert signals.is_suspicious
    assert len(signals.suspicion_reasons) >= 2


def test_display_name_impersonation_is_scored() -> None:
    signals = detect(
        make_message(
            sender="attacker@evil.example",
            sender_name="Chase Support support@chase.com",
            subject="Notice",
        )
    )

    assert any("display name claims" in r for r in signals.suspicion_reasons)


def test_reply_to_redirect_is_a_weak_signal_only() -> None:
    """One mismatched Reply-To is common and legitimate — it must not convict."""
    signals = detect(
        make_message(sender="news@company.com", reply_to="team@othercompany.com")
    )

    assert signals.suspicion_score < SUSPICION_THRESHOLD
    assert not signals.is_suspicious


def test_phishing_wording_alone_does_not_convict() -> None:
    """Real services do say "verify your account". Two signals are required."""
    signals = detect(
        make_message(sender="support@realcompany.com", subject="Verify your account")
    )

    assert not signals.is_suspicious


def test_public_provider_list_covers_the_big_ones() -> None:
    for domain in ("gmail.com", "yahoo.com", "outlook.com", "icloud.com", "proton.me"):
        assert domain in PUBLIC_EMAIL_PROVIDERS
