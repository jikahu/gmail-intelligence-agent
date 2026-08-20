"""Protection rules (CLAUDE.md §8).

Protection answers one narrow question: **may this email be routed to Review?**
It does not decide where the message ends up. A protected receipt is still
archived into ``Purchases-Receipts`` — protection only means it can't be
swept into the Review queue as low-value.

This is the single most safety-critical module in the app, because CLAUDE.md
§15 makes the launch gate "zero protected emails wrongly routed to Review".
The engine enforces that structurally: if :func:`evaluate` returns
``protected``, the Review branch is vetoed outright, whatever the other rules
think. Getting a topic keyword wrong can therefore only ever *fail open*
(email stays visible), never fail closed.

The one deliberate exception is security. A phishing message that impersonates
someone the user knows must still be flagged, so :data:`SECURITY_OVERRIDE`
lets suspicion outrank relationship protection — but even then the message is
archived to Review, never deleted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.classification import patterns
from app.classification.context import ClassificationContext, Rule
from app.classification.message import EmailMessage
from app.classification.signals import Signals

#: Security handling may override relationship protection (CLAUDE.md §7).
SECURITY_OVERRIDE = "security_override"


@dataclass(frozen=True)
class Protection:
    """Why (or whether) an email is shielded from the Review queue."""

    protected: bool = False
    reasons: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    is_vip: bool = False
    is_known_contact: bool = False
    is_prior_correspondent: bool = False
    is_active_thread: bool = False
    matched_rule: Rule | None = None
    #: Set when suspicion is strong enough to outrank relationship protection.
    security_override: bool = False

    @property
    def relationship_only(self) -> bool:
        """True when the only thing protecting this email is who sent it."""
        return self.protected and not self.topics


def _topic_matches(message: EmailMessage, signals: Signals) -> list[tuple[str, str]]:
    """Return ``(topic, matched_phrase)`` for every hard-protected topic present.

    Broad topics are matched against the subject and snippet rather than the
    whole body, so that a billing mention in a newsletter footer doesn't
    protect an advert. Security and fraud are matched against the full text,
    because missing a real security alert is the worse error.
    """
    headline = message.subject_and_snippet
    everything = message.searchable_text
    found: list[tuple[str, str]] = []

    # Security and fraud go first so they lead the reason list — "fraud alert"
    # is a better explanation to show the user than "financial content".
    for topic, pattern_set in (
        ("fraud", patterns.FRAUD),
        ("security", patterns.SECURITY),
    ):
        phrase = pattern_set.first_match(everything)
        if phrase:
            found.append((topic, phrase))

    # Ordered most specific first, for the same reason.
    broad_topics = (
        ("financial", patterns.FINANCIAL),
        ("legal_government", patterns.LEGAL_GOVERNMENT),
        ("medical", patterns.MEDICAL),
        ("insurance", patterns.INSURANCE),
        ("travel", patterns.TRAVEL),
        ("purchase", patterns.PURCHASE),
        ("delivery", patterns.DELIVERY),
        ("career", patterns.CAREER),
        ("important_document", patterns.IMPORTANT_DOCUMENT),
        ("calendar", patterns.CALENDAR),
    )
    for topic, pattern_set in broad_topics:
        phrase = pattern_set.first_match(headline)
        if phrase:
            found.append((topic, phrase))

    # Education protects genuine material only. Marketing dressed up as a
    # course gets no protection (CLAUDE.md §7).
    education_phrase = patterns.EDUCATION.first_match(headline)
    if education_phrase and not signals.is_promotional:
        found.append(("education", education_phrase))

    return found


def evaluate(
    message: EmailMessage,
    signals: Signals,
    context: ClassificationContext,
) -> Protection:
    """Decide whether this email is shielded from Review, and say why."""
    reasons: list[str] = []
    topics: list[str] = []

    # -- Explicit user rules outrank everything else -------------------
    matched_rule = context.sender_rule_for(message.sender_email)
    if matched_rule is None:
        matched_rule = context.domain_rule_for(message.sender_domain)
    if matched_rule is None:
        # Fall back to the registrable domain so a rule on `chase.com` also
        # covers `alerts.chase.com`.
        matched_rule = context.domain_rule_for(message.sender_registrable_domain)

    if matched_rule is not None and matched_rule.is_whitelist:
        reasons.append(f"you approved this {matched_rule.scope} ({matched_rule.target})")

    # -- VIP ------------------------------------------------------------
    is_vip = context.is_vip(message.sender_email)
    if is_vip:
        reasons.append(f"{message.sender_email} is an approved VIP")

    # -- Substack ------------------------------------------------------
    # CLAUDE.md §9: bulk-mail signals "do not override Substack". Substack
    # newsletters carry List-Unsubscribe like every other mass mailing, so
    # without this they would be swept up by the generic bulk rule.
    # Recorded as a reason rather than a topic, so a message merely
    # *impersonating* Substack can still be caught by the security override.
    if signals.is_substack:
        reasons.append("Substack newsletter, which you keep by default")

    # -- Relationship ---------------------------------------------------
    is_known_contact = context.is_known_contact(message.sender_email)
    if is_known_contact:
        reasons.append("sender is in your Google Contacts")

    is_prior = context.is_prior_correspondent(message.sender_email)
    if is_prior:
        reasons.append("you have emailed this sender before")

    is_active_thread = message.is_active_thread
    if is_active_thread:
        reasons.append("this is a conversation you're taking part in")

    # -- Hard-protected topics -----------------------------------------
    for topic, phrase in _topic_matches(message, signals):
        topics.append(topic)
        reasons.append(f"{topic.replace('_', ' ')} content ({phrase!r})")

    # -- Attachments ----------------------------------------------------
    if message.has_attachments:
        topics.append("attachment")
        names = ", ".join(a.filename for a in message.attachments[:3])
        reasons.append(f"has an attachment ({names})")

    protected = bool(reasons) and matched_rule_allows_protection(matched_rule)

    # -- Security override ---------------------------------------------
    # Strong phishing signals beat relationship protection, but only when the
    # protection is *relational*. A genuine bank alert stays protected.
    override = False
    if signals.is_suspicious and protected and not topics:
        override = True
        protected = False
        reasons.append(
            "protection set aside: this message has strong phishing indicators"
        )

    return Protection(
        protected=protected,
        reasons=tuple(reasons),
        topics=tuple(dict.fromkeys(topics)),
        is_vip=is_vip,
        is_known_contact=is_known_contact,
        is_prior_correspondent=is_prior,
        is_active_thread=is_active_thread,
        matched_rule=matched_rule,
        security_override=override,
    )


def matched_rule_allows_protection(rule: Rule | None) -> bool:
    """A blacklist rule is the user saying "I don't want this" — respect it."""
    return not (rule is not None and rule.is_blacklist)


__all__ = ("Protection", "SECURITY_OVERRIDE", "evaluate")
