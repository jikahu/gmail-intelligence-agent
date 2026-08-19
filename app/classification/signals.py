"""Structural signals derived from one email.

These are the observations the rules engine reasons about: is this a bulk
mailing, does it carry list headers, does the sender look like a robot, does
anything about it look like phishing. Every signal is cheap, deterministic,
and explains itself — each one records *why* it fired so the dashboard and the
audit log can show a reason rather than a verdict.

No signal decides anything on its own. :mod:`app.classification.engine` is
what weighs them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.classification import patterns
from app.classification.message import (
    GMAIL_CATEGORY_FORUMS,
    GMAIL_CATEGORY_PROMOTIONS,
    GMAIL_CATEGORY_SOCIAL,
    EmailMessage,
    domain_of,
    registrable_domain,
)

#: Local-parts that mean "a machine sent this, don't reply".
_ROBOT_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
        "do_not_reply", "notification", "notifications", "notify",
        "alert", "alerts", "mailer", "mailer-daemon", "postmaster",
        "bounce", "bounces", "automated", "auto", "system", "robot",
        "news", "newsletter", "updates", "info", "hello", "team",
    }
)

#: Headers that only appear on mass mailings.
_BULK_HEADERS: tuple[tuple[str, str], ...] = (
    ("list-unsubscribe", "carries a List-Unsubscribe header"),
    ("list-id", "carries a List-Id header"),
    ("list-post", "carries a List-Post header"),
    ("x-campaign-id", "carries a campaign ID header"),
    ("x-mailchimp-id", "sent through Mailchimp"),
    ("feedback-id", "carries a bulk Feedback-ID header"),
    ("x-ses-outgoing", "sent through Amazon SES"),
    ("x-sg-eid", "sent through SendGrid"),
)

_BULK_PRECEDENCE_VALUES: frozenset[str] = frozenset({"bulk", "list", "junk"})

#: A suspicion score at or above this routes the message to Suspicious+Review.
SUSPICION_THRESHOLD: int = 3


@dataclass(frozen=True)
class Signals:
    """What we can tell about a message from its structure alone."""

    is_bulk: bool = False
    bulk_reasons: tuple[str, ...] = ()
    has_unsubscribe: bool = False
    has_list_headers: bool = False
    is_automated_sender: bool = False
    is_newsletter: bool = False
    is_substack: bool = False
    is_promotional: bool = False
    is_social_notification: bool = False
    is_forum: bool = False
    suspicion_score: int = 0
    suspicion_reasons: tuple[str, ...] = ()
    promotional_terms: tuple[str, ...] = ()

    @property
    def is_suspicious(self) -> bool:
        return self.suspicion_score >= SUSPICION_THRESHOLD

    @property
    def is_mass_mail(self) -> bool:
        """Bulk *or* promotional — the broad "not written to me personally" test."""
        return self.is_bulk or self.is_promotional


def _local_part(email_address: str) -> str:
    local, _, _ = (email_address or "").partition("@")
    return local.strip().lower()


def _detect_bulk(message: EmailMessage) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []

    for header, explanation in _BULK_HEADERS:
        if message.has_header(header):
            reasons.append(explanation)

    precedence = message.header("precedence").strip().lower()
    if precedence in _BULK_PRECEDENCE_VALUES:
        reasons.append(f"Precedence: {precedence}")

    auto_submitted = message.header("auto-submitted").strip().lower()
    if auto_submitted and auto_submitted != "no":
        reasons.append("marked Auto-Submitted")

    if message.header("x-auto-response-suppress"):
        reasons.append("suppresses auto-responses (bulk sender behaviour)")

    # An undisclosed recipient list is a mass-mail shape, but it is far too
    # weak to stand alone — plenty of ordinary mail arrives Bcc'd, and a
    # missing To header in a fixture or a partial fetch must not by itself
    # push a message toward Review. It only corroborates other evidence.
    if reasons and not message.to and not message.cc:
        reasons.append("no visible recipient")

    return bool(reasons), tuple(reasons)


def _detect_substack(message: EmailMessage) -> bool:
    domain = message.sender_domain
    if any(
        domain == known or domain.endswith(f".{known}")
        for known in patterns.SUBSTACK_DOMAINS
    ):
        return True
    # Substack sends from custom domains but keeps its List-Id.
    haystack = " ".join(
        (
            message.header("list-id"),
            message.header("list-unsubscribe"),
            message.header("list-post"),
            message.header("x-mailer"),
        )
    ).lower()
    return "substack" in haystack


def _detect_suspicion(message: EmailMessage) -> tuple[int, tuple[str, ...]]:
    """Score phishing-like traits. Higher is worse; 3+ is acted on."""
    score = 0
    reasons: list[str] = []

    tld = message.sender_domain.rsplit(".", 1)[-1] if message.sender_domain else ""
    if tld in patterns.SUSPICIOUS_TLDS:
        score += 2
        reasons.append(f"sender domain ends in .{tld}, a high-abuse suffix")

    phishing_phrase = patterns.PHISHING.first_match(message.searchable_text)
    if phishing_phrase:
        score += 2
        reasons.append(f"uses phishing-style wording ({phishing_phrase!r})")

    # A display name that contains a *different* address than the real sender.
    display_name = message.sender_name or ""
    if "@" in display_name:
        claimed_domain = domain_of(display_name.split()[-1].strip("<>"))
        if claimed_domain and claimed_domain != message.sender_domain:
            score += 2
            reasons.append(
                f"display name claims {claimed_domain!r} but the message came "
                f"from {message.sender_domain!r}"
            )

    # Reply-To pointing somewhere else entirely is a classic redirect. Compare
    # registrable domains, not exact strings — a sender on a dedicated
    # sub-domain (mail.anthropic.com) replying through its main domain
    # (anthropic.com) is normal bulk-mail infrastructure, not a redirect.
    reply_domain = domain_of(message.reply_to)
    if reply_domain and registrable_domain(reply_domain) != registrable_domain(
        message.sender_domain
    ):
        score += 1
        reasons.append(
            f"replies would go to {reply_domain!r}, not {message.sender_domain!r}"
        )

    # Urgency plus a credential or money ask.
    if patterns.P1_URGENT.matches(message.subject_and_snippet) and phishing_phrase:
        score += 1
        reasons.append("combines urgency with an account or payment request")

    if not message.sender_domain:
        score += 1
        reasons.append("no parseable sender domain")

    return score, tuple(reasons)


def detect(message: EmailMessage) -> Signals:
    """Derive all structural signals for one message."""
    is_bulk, bulk_reasons = _detect_bulk(message)
    has_unsubscribe = message.has_header("list-unsubscribe") or (
        "unsubscribe" in message.body_text.lower()
    )
    has_list_headers = message.has_header("list-id") or message.has_header(
        "list-unsubscribe"
    )
    is_substack = _detect_substack(message)

    promotional_terms = tuple(
        patterns.PROMOTIONAL.all_matches(message.subject_and_snippet)
    )
    is_promotional = bool(promotional_terms) or (
        GMAIL_CATEGORY_PROMOTIONS in message.label_ids
    )

    is_newsletter = is_substack or (
        has_list_headers
        and not is_promotional
        and message.header("list-id") != ""
    )

    suspicion_score, suspicion_reasons = _detect_suspicion(message)

    return Signals(
        is_bulk=is_bulk,
        bulk_reasons=bulk_reasons,
        has_unsubscribe=has_unsubscribe,
        has_list_headers=has_list_headers,
        is_automated_sender=_local_part(message.sender_email) in _ROBOT_LOCAL_PARTS,
        is_newsletter=is_newsletter,
        is_substack=is_substack,
        is_promotional=is_promotional,
        is_social_notification=GMAIL_CATEGORY_SOCIAL in message.label_ids,
        is_forum=GMAIL_CATEGORY_FORUMS in message.label_ids,
        suspicion_score=suspicion_score,
        suspicion_reasons=suspicion_reasons,
        promotional_terms=promotional_terms,
    )


__all__ = ("SUSPICION_THRESHOLD", "Signals", "detect")
