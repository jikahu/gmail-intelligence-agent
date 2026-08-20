"""The golden dataset (CLAUDE.md §15).

> Golden dataset support: examples with known correct classifications, used
> to compare classifier versions.

25 hand-labeled, realistic examples spanning every category CLAUDE.md §15
names for the 250-email acceptance sample, plus the protection mechanisms
that matter most: relationship protection (VIP, known contact, prior
correspondent, active thread, starring), the attachment hard-protection
overriding obviously promotional wording, and the priority veto overriding
Review for a P2 message that would otherwise have looked like bulk mail.

Every expectation here was checked against the actual deterministic engine
(``app/classification/engine.py``, ``protection.py``, ``signals.py``,
``patterns.py``) rather than guessed — see ``test_golden_dataset.py``, which
runs this dataset on every ``pytest`` pass and is the permanent regression
check CLAUDE.md §15 asks the golden dataset to provide.
"""

from __future__ import annotations

from app.classification.golden import GoldenExample, GoldenExpectation
from app.classification.context import ClassificationContext
from tests.fixtures.emails import bulk_headers, make_message, pdf, substack_headers

#: Shared context for every example: one VIP, one known contact, one prior
#: correspondent. Examples that shouldn't be protected deliberately use
#: senders outside this set.
GOLDEN_CONTEXT = ClassificationContext(
    user_email="jikahu@gmail.com",
    vip_emails={"vip@example.com"},
    known_contacts={"contact@example.com"},
    prior_correspondents={"friend@example.com"},
)


def _ex(category: str, expected: GoldenExpectation, **kwargs) -> GoldenExample:
    return GoldenExample(message=make_message(**kwargs), expected=expected, category=category)


GOLDEN_EXAMPLES: tuple[GoldenExample, ...] = (
    # ---- Financial (protected) ----
    _ex(
        "financial",
        GoldenExpectation(
            expected_labels=frozenset({"Financial"}),
            expected_priority="P2",
            expect_review=False,
            expect_protected=True,
            note="bank statement",
        ),
        sender="statements@mybank.com",
        subject="Your monthly statement is ready",
        body="Your account statement is now available. Current balance: $2,304.19.",
    ),
    _ex(
        "financial",
        GoldenExpectation(
            expected_labels=frozenset({"Financial", "Action-Required"}),
            expected_priority="P1",
            expect_review=False,
            expect_protected=True,
            note="payment failure",
        ),
        sender="billing@service.com",
        subject="Action needed: your payment failed",
        body="Your payment failed and your card was declined. Please update your payment method.",
    ),
    # ---- Security (protected) ----
    _ex(
        "security",
        GoldenExpectation(
            expected_labels=frozenset({"Security", "Critical"}),
            expected_priority="P1",
            expect_review=False,
            expect_protected=True,
            note="security alert",
        ),
        sender="security@bigapp.example",
        subject="Security alert: new sign-in to your account",
        body="We noticed a new sign-in to your account from an unrecognized device.",
    ),
    # ---- Government (protected, no dedicated label in the taxonomy) ----
    _ex(
        "government",
        GoldenExpectation(
            expected_priority="P2",
            expect_review=False,
            expect_protected=True,
            note="official government notice",
        ),
        sender="notices@immigration.example.gov",
        subject="Official notice regarding your visa application",
        body="This is an official notice from the government regarding your visa status.",
    ),
    # ---- Personal (protected via prior correspondence) ----
    _ex(
        "personal",
        GoldenExpectation(
            expected_labels=frozenset({"Personal"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="prior correspondent, no topic",
        ),
        sender="friend@example.com",
        subject="Hey, are we still on for Saturday?",
        body="Just checking if we're still meeting up this weekend.",
    ),
    # ---- Work (ordinary business mail — correctly unprotected AND not reviewed) ----
    _ex(
        "work",
        GoldenExpectation(
            expected_labels=frozenset({"Work-Business"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=False,
            note="ordinary individually-addressed business mail needs no protection to stay out of Review",
        ),
        sender="colleague@othercompany.example",
        subject="Project update: Q3 deliverables",
        body="Sharing the latest status on the Q3 deliverables. Let me know your thoughts.",
    ),
    # ---- Career (protected) ----
    _ex(
        "career",
        GoldenExpectation(
            expected_labels=frozenset({"Career"}),
            expected_priority="P2",
            expect_review=False,
            expect_protected=True,
            note="interview invitation",
        ),
        sender="recruiting@techco.example",
        subject="Interview invitation for Software Engineer role",
        body="We'd like to invite you to interview for the Software Engineer position.",
    ),
    # ---- Receipts / purchases (protected) ----
    _ex(
        "receipts",
        GoldenExpectation(
            expected_labels=frozenset({"Purchases-Receipts"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="plain order confirmation",
        ),
        sender="orders@shop.example",
        subject="Your order confirmation - order #48213",
        body="Thank you for your order! Your order has shipped and is on its way.",
    ),
    _ex(
        "purchases",
        GoldenExpectation(
            expected_labels=frozenset({"Purchases-Receipts", "Action-Required"}),
            expected_priority="P2",
            expect_review=False,
            expect_protected=True,
            note="delivery problem escalates to Action Required",
        ),
        sender="shipping@shop.example",
        subject="Delivery delayed - action needed for order #123",
        body="Your delivery attempt failed. Please provide additional instructions.",
    ),
    # ---- Travel (protected) ----
    _ex(
        "travel",
        GoldenExpectation(
            expected_labels=frozenset({"Purchases-Receipts"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="flight itinerary — travel bookings fold into Purchases-Receipts (Phase 6 groups the trip)",
        ),
        sender="reservations@airline.example",
        subject="Your flight itinerary and boarding pass",
        body="Here is your itinerary for the upcoming trip, including your seat assignment.",
    ),
    # ---- Educational ----
    _ex(
        "educational",
        GoldenExpectation(
            expected_labels=frozenset({"Education"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="genuine course material",
        ),
        sender="noreply@university.example",
        subject="Your course syllabus and first assignment",
        body="Welcome to the course! Attached are the syllabus and your first assignment.",
    ),
    _ex(
        "educational",
        GoldenExpectation(
            expect_review=True,
            expect_protected=False,
            note="marketing dressed up as education gets no protection (CLAUDE.md §7)",
        ),
        sender="promo@cryptocourse.example",
        subject="Free masterclass: learn to trade crypto - register now!",
        body="Join our free masterclass and learn to trade like a pro.",
    ),
    # ---- Substack (protected, always kept) ----
    _ex(
        "substack",
        GoldenExpectation(
            expected_labels=frozenset({"Newsletter"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="Substack is protected even though it carries bulk-mail headers",
        ),
        sender="writer@thoughtful.substack.com",
        subject="This week's essay: on slow mornings",
        body="A personal essay about mornings and mindfulness.",
        headers=substack_headers("thoughtful"),
    ),
    # ---- Other newsletters (default to Review) ----
    _ex(
        "other_newsletters",
        GoldenExpectation(
            expected_labels=frozenset({"Newsletter"}),
            expected_priority="P3",
            expect_review=True,
            expect_protected=False,
            note="non-Substack newsletter, not yet approved",
        ),
        sender="digest@randomblog.example",
        subject="This week's community digest",
        body="Read what's new in our community. Unsubscribe here.",
        headers=bulk_headers(),
    ),
    # ---- Starring rescues that same shape of newsletter ----
    _ex(
        "other_newsletters",
        GoldenExpectation(
            expected_labels=frozenset({"Newsletter"}),
            expect_review=False,
            expect_protected=True,
            note="the user starring a message overrides an otherwise-Review-bound newsletter",
        ),
        sender="digest@randomblog.example",
        subject="This week's community digest",
        body="Read what's new in our community. Unsubscribe here.",
        headers=bulk_headers(),
        labels=["INBOX", "STARRED"],
    ),
    # ---- Promotions ----
    _ex(
        "promotions",
        GoldenExpectation(
            expected_labels=frozenset({"Review", "Low-Value"}),
            expected_priority="P3",
            expect_review=True,
            expect_protected=False,
            note="flash sale",
        ),
        sender="deals@retailer.example",
        subject="Flash Sale: 40% off everything - today only!",
        body="Don't miss our biggest sale of the year! Shop now and save big.",
    ),
    # ---- Automated notifications ----
    _ex(
        "automated_notifications",
        GoldenExpectation(
            expect_review=True,
            expect_protected=False,
            note="engagement-bait digest from an automated sender",
        ),
        sender="notifications@socialapp.example",
        subject="Your weekly digest is here",
        body="Check out what's trending now.",
        headers=bulk_headers(),
    ),
    # ---- Cold outreach ----
    _ex(
        "cold_outreach",
        GoldenExpectation(
            expect_review=True,
            expect_protected=False,
            note="cold sales email",
        ),
        sender="sales@vendor.example",
        subject="Quick question about your email workflow",
        body="I wanted to follow up and see if you'd be open to a quick call to discuss how we can help grow your business.",
    ),
    # ---- Attachments (hard-protected even over promotional wording) ----
    _ex(
        "attachments",
        GoldenExpectation(
            expect_review=False,
            expect_protected=True,
            note=(
                "an attachment protects the message even though the subject reads "
                "as promotional — CLAUDE.md §8's hard protection at work"
            ),
        ),
        sender="random@example.com",
        subject="Sale ends today - 50% off!!!",
        body="Massive sale today only, shop now!",
        attachments=[pdf(filename="flyer.pdf")],
    ),
    # ---- Suspicious / phishing ----
    _ex(
        "suspicious",
        GoldenExpectation(
            expected_labels=frozenset({"Suspicious", "Review"}),
            expect_review=True,
            expect_protected=False,
            note="high suspicion score: bad TLD, phishing wording, mismatched reply-to, urgency",
        ),
        sender="security@paypa1-verify.top",
        subject="Verify your account immediately or it will be suspended",
        body=(
            "Click here to verify your account. Your account will be suspended if "
            "you don't act now. Enter your password to confirm."
        ),
        reply_to="reply@totally-different-domain.biz",
    ),
    # ---- Active thread (protected via relationship) ----
    _ex(
        "active_threads",
        GoldenExpectation(
            expected_labels=frozenset({"Personal"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="an ongoing back-and-forth the user is part of",
        ),
        sender="teammate@company.example",
        subject="Re: Budget review",
        body="Thanks for the update, let's finalize numbers tomorrow.",
        thread_message_count=4,
        user_in_thread=True,
    ),
    # ---- VIP (protected via relationship) ----
    _ex(
        "personal",
        GoldenExpectation(
            expected_labels=frozenset({"Personal"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="approved VIP sender",
        ),
        sender="vip@example.com",
        subject="Let's catch up soon",
        body="Would love to grab coffee sometime next week.",
    ),
    # ---- Known contact (protected via relationship) ----
    _ex(
        "personal",
        GoldenExpectation(
            expected_labels=frozenset({"Personal"}),
            expected_priority="P3",
            expect_review=False,
            expect_protected=True,
            note="Google Contacts sender",
        ),
        sender="contact@example.com",
        subject="Are you free this weekend?",
        body="Just checking in, hope you're doing well.",
    ),
    # ---- Expired (Review, never deleted) ----
    _ex(
        "other_newsletters",
        GoldenExpectation(
            expected_labels=frozenset({"Review", "Expired"}),
            expect_review=True,
            expect_protected=False,
            note="expired promo code",
        ),
        sender="promo@shop.example",
        subject="This offer has expired",
        body="Sorry, this promo code has expired and is no longer valid.",
        headers=bulk_headers(),
    ),
    # ---- Material change (priority veto, not protection, overrides Review) ----
    _ex(
        "work",
        GoldenExpectation(
            expected_labels=frozenset({"Subscription-Review"}),
            expected_priority="P2",
            expect_review=False,
            expect_protected=False,
            note=(
                "bulk-mail signals alone would suggest Review, but the P2 material-"
                "change priority vetoes it — CLAUDE.md §10; demonstrates the "
                "priority veto rather than the protection veto"
            ),
        ),
        sender="billing@service.example",
        subject="Important changes to your subscription pricing",
        body="Effective next month, your plan price will increase from $9.99 to $12.99.",
        headers=bulk_headers(),
    ),
)


__all__ = ("GOLDEN_CONTEXT", "GOLDEN_EXAMPLES")
