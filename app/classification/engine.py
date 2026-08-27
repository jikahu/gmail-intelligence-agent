"""The deterministic rules engine — steps 1 to 7 of CLAUDE.md §11.

Order of operations, and it is not negotiable:

1. Gmail metadata → structural signals
2. Explicit manual rules (sender, then domain)
3. VIP rules
4. Contacts / relationship
5. Hard protection
6. Deterministic classification
7. Behavioural signals
   ↓
   *Review decision, with the protection veto applied last*

Steps 8 (AI) and 10 (acting on Gmail) are later phases. This module decides;
nothing here touches Gmail.

The safety property that matters: **the Review branch is vetoed by protection,
structurally.** It isn't a keyword competing with other keywords — protection
is evaluated first and the Review decision cannot overrule it, except for the
security override in :mod:`app.classification.protection`. That's what makes
the CLAUDE.md §15 launch gate (zero protected emails wrongly sent to Review)
an architectural guarantee rather than a matter of tuning word lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.classification import patterns, signals as signals_module
from app.classification.context import ClassificationContext, Rule, VendorRule
from app.classification.labels import (
    IMPORTANT_LABELS,
    Label,
    Priority,
    combine_policies,
    gmail_labels,
)
from app.classification.message import EmailMessage
from app.classification.protection import Protection
from app.classification.protection import evaluate as evaluate_protection
from app.classification.signals import Signals
from app.logging_config import get_logger

log = get_logger("app.classification.engine")

# Confidence bands for deterministic outcomes. AI-assisted confidence arrives
# in Phase 4 and is always validated against these rules, never above them.
CONFIDENCE_EXPLICIT_RULE = 0.99
CONFIDENCE_STRONG_TOPIC = 0.90
CONFIDENCE_STRUCTURAL = 0.80
CONFIDENCE_FALLBACK = 0.60
CONFIDENCE_UNRESOLVED = 0.40

#: Attachment types that can constitute a long-term record.
_DOCUMENT_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    }
)
_DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")

#: Labels that answer "what kind of email is this?". Modifier labels such as
#: Subscription-Review and Important-Document are deliberately excluded.
_CATEGORY_LABELS: frozenset[Label] = frozenset(
    {
        Label.SECURITY,
        Label.CRITICAL,
        Label.FINANCIAL,
        Label.PURCHASES_RECEIPTS,
        Label.EDUCATION,
        Label.CAREER,
        Label.PERSONAL,
        Label.WORK_BUSINESS,
    }
)


@dataclass
class Classification:
    """The engine's decision about one email. Nothing has been applied yet."""

    message_id: str = ""
    thread_id: str = ""
    labels: set[Label] = field(default_factory=set)
    priority: Priority = Priority.P3_NORMAL
    keep_in_inbox: bool = True
    archive: bool = False
    mark_important: bool = False
    review: bool = False
    review_reason: str | None = None
    action_required: bool = False
    protected: bool = False
    protection_reasons: tuple[str, ...] = ()
    confidence: float = CONFIDENCE_FALLBACK
    rules_triggered: tuple[str, ...] = ()
    #: True when the deterministic rules couldn't settle it — Phase 4's cue.
    needs_ai: bool = False
    rationale: str = ""
    sent_by_user: bool = False
    #: Set when a config vendor rule (CLAUDE.md §11) matched: the existing
    #: Gmail label to apply instead of a category label like Financial. Never
    #: created if it doesn't already exist -- same guarantee as the automatic
    #: sender-based vendor matcher in :mod:`app.gmail.vendor_labels`.
    forced_vendor_label: str | None = None

    @property
    def gmail_label_names(self) -> list[str]:
        """Labels that may actually be written to Gmail (internal ones dropped)."""
        return gmail_labels(self.labels)

    @property
    def is_important(self) -> bool:
        return bool(self.labels & IMPORTANT_LABELS)

    def has(self, label: Label) -> bool:
        return label in self.labels

    def summary(self) -> str:
        """One-line, log-safe description of the outcome."""
        names = ", ".join(self.gmail_label_names) or "(no labels)"
        where = "Inbox" if self.keep_in_inbox else ("Review" if self.review else "Archive")
        return f"{self.priority.value} · {names} · → {where}"


# --------------------------------------------------------------------
# Step 2 — explicit manual rules
# --------------------------------------------------------------------


def _label_from_action(action: str) -> Label | None:
    """Turn a rule's ``action`` cell into a Label, tolerating loose spelling
    and a stray old-style ``AI/`` prefix (the taxonomy dropped it, but a rule
    copied over from an earlier config shouldn't silently stop working)."""
    candidate = (action or "").strip().lower()
    if candidate.startswith("ai/"):
        candidate = candidate[len("ai/"):]
    if not candidate:
        return None
    for label in Label:
        if candidate in (label.value.lower(), label.name.lower()):
            return label
    log.warning("rule_action_not_a_label", extra={"rule_action": action})
    return None


def _apply_explicit_rule(
    rule: Rule | None, labels: set[Label], triggered: list[str]
) -> bool:
    """Apply a matching manual rule. Returns True if it decided the category."""
    if rule is None:
        return False

    triggered.append(rule.describe())
    forced = _label_from_action(rule.action)
    if forced is not None:
        labels.add(forced)
        return True
    return False


def _match_vendor_rule(
    message: EmailMessage, context: ClassificationContext
) -> VendorRule | None:
    """First ``config/rules.toml`` vendor rule (CLAUDE.md §11) this message
    matches, or ``None``. ``subject_contains`` checks the subject only;
    ``sender_contains`` checks the sender's domain *and* display name --
    either counts."""
    subject = message.subject.lower()
    sender_domain = message.sender_domain.lower()
    sender_name = message.sender_name.lower()

    for rule in context.vendor_rules:
        if rule.match == "subject_contains" and rule.value in subject:
            return rule
        if rule.match == "sender_contains" and (
            rule.value in sender_domain or rule.value in sender_name
        ):
            return rule
    return None


# --------------------------------------------------------------------
# Step 6 — deterministic classification
# --------------------------------------------------------------------


def _assign_categories(
    message: EmailMessage,
    signals: Signals,
    protection: Protection,
    labels: set[Label],
    triggered: list[str],
) -> None:
    """Add category labels based on protected topics and structure."""
    topics = set(protection.topics)
    headline = message.subject_and_snippet

    if "security" in topics or "fraud" in topics:
        labels.update({Label.SECURITY, Label.CRITICAL})
        triggered.append("security content → Security + Critical")

    if "fraud" in topics:
        labels.update({Label.FINANCIAL, Label.ACTION_REQUIRED})
        triggered.append("fraud indicators → Financial + Action-Required")

    if "financial" in topics:
        labels.add(Label.FINANCIAL)
        triggered.append("financial content → Financial")

    if "purchase" in topics or "delivery" in topics:
        labels.add(Label.PURCHASES_RECEIPTS)
        triggered.append("order/delivery content → Purchases-Receipts")

    if "travel" in topics:
        labels.add(Label.PURCHASES_RECEIPTS)
        triggered.append("travel booking → Purchases-Receipts")

    if "education" in topics:
        labels.add(Label.EDUCATION)
        triggered.append("genuine educational content → Education")

    if "career" in topics:
        labels.add(Label.CAREER)
        triggered.append("career content → Career")

    # An attachment always *protects* a message (§8), but that alone doesn't
    # make it a long-term record — a promotional PDF is still an advert. The
    # label needs positive evidence: a document-shaped attachment (by mime
    # type or filename) plus some other topic already matched, or subject
    # wording that says so directly.
    document_attached = any(
        attachment.mime_type in _DOCUMENT_MIME_TYPES
        or attachment.filename.lower().endswith(_DOCUMENT_EXTENSIONS)
        for attachment in message.attachments
    )
    if patterns.IMPORTANT_DOCUMENT.matches(headline) or (
        document_attached and (topics - {"attachment"})
    ):
        labels.add(Label.IMPORTANT_DOCUMENT)
        triggered.append("long-term record → Important-Document")

    if patterns.SUBSCRIPTION.matches(headline):
        labels.add(Label.SUBSCRIPTION_REVIEW)
        triggered.append("subscription or renewal → Subscription-Review")

    # Only label it a newsletter if nothing more specific applied. Bank
    # statements, receipts and booking confirmations routinely carry the same
    # List-Id header a newsletter does — labelling those "Newsletter" would be
    # both wrong and confusing. Substack is always labelled, because that's
    # what it is.
    if signals.is_newsletter and (
        signals.is_substack or not (labels & _CATEGORY_LABELS)
    ):
        labels.add(Label.NEWSLETTER)
        triggered.append(
            "Substack newsletter → Newsletter"
            if signals.is_substack
            else "newsletter list headers → Newsletter"
        )

    if signals.is_suspicious:
        labels.update({Label.SUSPICIOUS, Label.REVIEW})
        triggered.append("phishing indicators → Suspicious + Review")

    # Fallback: nothing categorical matched.
    if not labels:
        personal_ish = (
            protection.is_vip
            or protection.is_known_contact
            or protection.is_prior_correspondent
            or protection.is_active_thread
        )
        # Note: structural bulk, not promotional *wording*. A friend writing
        # "check out this sale" is still a personal email.
        if personal_ish and not signals.is_bulk:
            labels.add(Label.PERSONAL)
            triggered.append("known correspondent, written individually → Personal")
        elif not signals.is_mass_mail and not signals.is_automated_sender:
            labels.add(Label.WORK_BUSINESS)
            triggered.append("individually-addressed mail → Work-Business")


def _detect_action_required(
    message: EmailMessage, labels: set[Label], triggered: list[str]
) -> bool:
    """Per-category action detection (CLAUDE.md §7)."""
    headline = message.subject_and_snippet
    phrase = patterns.ACTION_REQUIRED.first_match(headline)

    payment_problem = any(
        term in headline
        for term in ("payment failed", "payment declined", "past due", "overdue")
    )

    delivery_problem = any(
        term in headline
        for term in ("delivery delayed", "delivery attempt", "action needed")
    )

    if phrase:
        triggered.append(f"action wording ({phrase!r}) → Action-Required")
    if payment_problem:
        triggered.append("payment problem → Action-Required")
    if delivery_problem:
        triggered.append("delivery problem → Action-Required")

    required = bool(phrase or payment_problem or delivery_problem)
    if required:
        labels.add(Label.ACTION_REQUIRED)
    return required


def _assign_priority(
    message: EmailMessage,
    signals: Signals,
    protection: Protection,
    labels: set[Label],
    action_required: bool,
    triggered: list[str],
) -> Priority:
    """P1/P2/P3 (CLAUDE.md §7). Urgency wording alone is never enough for P1."""
    topics = set(protection.topics)
    headline = message.subject_and_snippet
    everything = message.searchable_text
    urgent_phrase = patterns.P1_URGENT.first_match(headline)

    # --- P1 -----------------------------------------------------------
    if "fraud" in topics:
        triggered.append("fraud alert → P1")
        return Priority.P1_URGENT

    if "security" in topics and (
        urgent_phrase or patterns.SECURITY.matches(headline)
    ):
        triggered.append("security incident → P1")
        return Priority.P1_URGENT

    if any(
        term in everything
        for term in ("payment failed", "payment declined", "account locked",
                     "account suspended", "card was declined")
    ):
        triggered.append("account or payment failure → P1")
        return Priority.P1_URGENT

    if urgent_phrase and topics:
        # Urgency only counts when it's attached to something that matters —
        # otherwise every "URGENT: 50% off!" advert would be P1.
        triggered.append(f"urgent wording ({urgent_phrase!r}) on protected content → P1")
        return Priority.P1_URGENT

    # --- P2 -----------------------------------------------------------
    material_change = patterns.MATERIAL_CHANGE.first_match(headline)
    if material_change:
        triggered.append(f"material change ({material_change!r}) → P2")
        return Priority.P2_IMPORTANT

    p2_topics = topics & {
        "financial", "legal_government", "insurance", "medical", "career"
    }
    if p2_topics:
        triggered.append(f"{sorted(p2_topics)[0]} content → P2")
        return Priority.P2_IMPORTANT

    if action_required:
        triggered.append("action required → P2")
        return Priority.P2_IMPORTANT

    if Label.CRITICAL in labels:
        return Priority.P2_IMPORTANT

    return Priority.P3_NORMAL


# --------------------------------------------------------------------
# Review decision
# --------------------------------------------------------------------


def _review_candidate_reason(
    message: EmailMessage, signals: Signals, rule: Rule | None
) -> str | None:
    """Why this message *looks* like a Review candidate, before protection."""
    headline = message.subject_and_snippet

    if rule is not None and rule.is_blacklist:
        return f"you set a rule to review mail from {rule.target}"

    if signals.is_suspicious:
        return "this message has phishing indicators"

    if signals.is_promotional and signals.promotional_terms:
        return f"promotional wording ({', '.join(signals.promotional_terms[:3])})"

    if signals.is_social_notification:
        return "social network notification"

    for label, pattern_set in (
        ("cold sales outreach", patterns.COLD_SALES),
        ("engagement bait", patterns.ENGAGEMENT_BAIT),
        ("crypto promotion", patterns.CRYPTO_PROMO),
        ("webinar promotion", patterns.WEBINAR_PROMO),
    ):
        phrase = pattern_set.first_match(headline)
        if phrase:
            return f"{label} ({phrase!r})"

    expired_phrase = patterns.EXPIRED.first_match(headline)
    if expired_phrase:
        return f"looks expired or already completed ({expired_phrase!r})"

    if signals.is_newsletter and not signals.is_substack:
        return "newsletter from a sender you haven't approved yet"

    if signals.is_bulk:
        return "bulk mailing with nothing that needs your attention"

    return None


# --------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------


def classify(
    message: EmailMessage, context: ClassificationContext | None = None
) -> Classification:
    """Run the full deterministic pipeline for one email."""
    context = context or ClassificationContext()
    triggered: list[str] = []
    labels: set[Label] = set()

    # Step 1 — Gmail metadata → structural signals.
    signals = signals_module.detect(message)

    if message.sent_by_user:
        return Classification(
            message_id=message.message_id,
            thread_id=message.thread_id,
            sent_by_user=True,
            confidence=CONFIDENCE_EXPLICIT_RULE,
            rationale="You sent this message; it isn't classified as incoming mail.",
            rules_triggered=("sent by the user",),
        )

    # Steps 2-5 — explicit rules, VIP, relationship, hard protection.
    protection = evaluate_protection(message, signals, context)
    rule = protection.matched_rule
    rule_decided = _apply_explicit_rule(rule, labels, triggered)

    # An exact sender/domain rule already decided it -- that outranks a
    # substring-based vendor rule, so only look for one otherwise.
    forced_vendor_label: str | None = None
    if not rule_decided:
        vendor_rule = _match_vendor_rule(message, context)
        if vendor_rule is not None:
            rule_decided = True
            forced_vendor_label = vendor_rule.label
            triggered.append(f"{vendor_rule.describe()}, categorization skipped")

    if protection.is_vip:
        triggered.append("approved VIP sender")
    if protection.protected:
        triggered.extend(f"protected: {reason}" for reason in protection.reasons)

    # Step 6 — deterministic classification.
    if not rule_decided:
        _assign_categories(message, signals, protection, labels, triggered)

    action_required = _detect_action_required(message, labels, triggered)
    priority = _assign_priority(
        message, signals, protection, labels, action_required, triggered
    )

    # Step 7 — behavioural signals. Explicit user decisions already outrank
    # these, so the only thing they do here is protect: a starred message is
    # one the user has actively marked as mattering.
    if message.is_starred and not protection.protected:
        protection = Protection(
            protected=True,
            reasons=protection.reasons + ("you starred this message",),
            topics=protection.topics,
            is_vip=protection.is_vip,
            is_known_contact=protection.is_known_contact,
            is_prior_correspondent=protection.is_prior_correspondent,
            is_active_thread=protection.is_active_thread,
            matched_rule=protection.matched_rule,
        )
        triggered.append("protected: you starred this message")

    # -- Review decision, protection applied last ----------------------
    review_reason = _review_candidate_reason(message, signals, rule)
    review = review_reason is not None

    if review and protection.protected and not signals.is_suspicious:
        triggered.append(
            f"Review vetoed by protection ({review_reason}) — kept because "
            f"{protection.reasons[0]}"
        )
        review = False
        review_reason = None

    # Anything the engine itself rated P1 or P2 is, by its own reckoning,
    # important — so it must not then be hidden in the Review queue. This
    # catches the case where a message earns priority from something that
    # isn't a protected *topic*, such as a price increase or a terms change
    # (CLAUDE.md §10 material changes, §21 visibility over tidiness).
    if review and priority is not Priority.P3_NORMAL and not signals.is_suspicious:
        triggered.append(
            f"Review vetoed by {priority.value} priority ({review_reason})"
        )
        review = False
        review_reason = None

    if review:
        labels.add(Label.REVIEW)
        if patterns.EXPIRED.matches(message.subject_and_snippet):
            labels.add(Label.EXPIRED)
        if not labels - {Label.REVIEW, Label.EXPIRED}:
            labels.add(Label.LOW_VALUE)

    # -- Resolve placement ---------------------------------------------
    policy = combine_policies(labels) if labels else combine_policies({Label.PERSONAL})

    # Anything urgent stays where the user will see it, whatever its category
    # says. A cancelled flight is a travel booking (normally archived) but it
    # is also a P1 — visibility wins (CLAUDE.md §21).
    keep_in_inbox = policy.keep_in_inbox
    archive = policy.archive

    if priority is Priority.P1_URGENT and not review:
        if not keep_in_inbox:
            triggered.append("P1 urgency keeps this in the Inbox")
        keep_in_inbox, archive = True, False

    # Personal mail isn't marked Important unless it's genuinely urgent
    # or actionable (CLAUDE.md §7).
    mark_important = policy.mark_important
    if (
        labels == {Label.PERSONAL}
        and priority is Priority.P3_NORMAL
        and not action_required
    ):
        mark_important = False

    confidence, needs_ai = _confidence_for(
        rule_decided=rule_decided,
        protection=protection,
        labels=labels,
        review=review,
        signals=signals,
    )

    classification = Classification(
        message_id=message.message_id,
        thread_id=message.thread_id,
        labels=labels,
        priority=priority,
        keep_in_inbox=keep_in_inbox and not review,
        archive=archive or review,
        mark_important=mark_important or priority is Priority.P1_URGENT,
        review=review,
        review_reason=review_reason,
        action_required=action_required,
        protected=protection.protected,
        protection_reasons=protection.reasons,
        confidence=confidence,
        rules_triggered=tuple(triggered),
        needs_ai=needs_ai,
        rationale=_rationale(labels, priority, review, review_reason, protection),
        forced_vendor_label=forced_vendor_label,
    )

    _assert_safety_invariants(classification, signals)
    return classification


def _confidence_for(
    rule_decided: bool,
    protection: Protection,
    labels: set[Label],
    review: bool,
    signals: Signals,
) -> tuple[float, bool]:
    """Return ``(confidence, needs_ai)``."""
    if rule_decided:
        return CONFIDENCE_EXPLICIT_RULE, False
    if not labels:
        # Nothing matched at all — this is exactly what Phase 4's AI step is
        # for. Checked before topic strength, because "the sender attached a
        # PDF" is a strong protection signal but tells us nothing about what
        # the message actually is.
        return CONFIDENCE_UNRESOLVED, True
    # "attachment" is excluded on purpose: an attached PDF is a strong reason
    # to protect a message but tells us nothing about what the message *is*,
    # so it must not inflate confidence in the category we picked.
    if set(protection.topics) - {"attachment"}:
        return CONFIDENCE_STRONG_TOPIC, False
    if review and (signals.is_bulk or signals.is_promotional or signals.is_suspicious):
        return CONFIDENCE_STRUCTURAL, False
    if signals.is_substack or any(
        (
            protection.is_vip,
            protection.is_known_contact,
            protection.is_prior_correspondent,
            protection.is_active_thread,
        )
    ):
        # Who sent it is a fact, not a guess.
        return CONFIDENCE_STRUCTURAL, False
    return CONFIDENCE_FALLBACK, not review


def _rationale(
    labels: set[Label],
    priority: Priority,
    review: bool,
    review_reason: str | None,
    protection: Protection,
) -> str:
    """A short, user-facing explanation. Never internal chain-of-thought."""
    if review and review_reason:
        return f"Moved to Review: {review_reason}."
    if protection.protected and protection.reasons:
        return f"Kept because {protection.reasons[0]}."
    if labels:
        names = ", ".join(sorted(label.value for label in labels))
        return f"Classified as {names} at {priority.value}."
    return "No deterministic rule matched; awaiting a second opinion."


def _assert_safety_invariants(
    classification: Classification, signals: Signals
) -> None:
    """Fail loudly if the engine ever violates a CLAUDE.md safety rule.

    These are cheap checks on our own output. They exist because a silent
    regression here is exactly the failure the launch gate is meant to catch,
    and a crash in a dry run is far better than a hidden email.
    """
    if classification.protected and classification.review and not signals.is_suspicious:
        raise AssertionError(
            "Safety violation: a protected email was routed to Review "
            f"({classification.message_id})."
        )
    if Label.TRASH_CANDIDATE in classification.labels:
        if Label.TRASH_CANDIDATE.value in classification.gmail_label_names:
            raise AssertionError(
                "Safety violation: Trash-Candidate must never reach Gmail."
            )
    if classification.review and classification.keep_in_inbox:
        raise AssertionError(
            "Inconsistent decision: a Review message cannot also stay in the Inbox."
        )
    if (
        classification.review
        and classification.priority is not Priority.P3_NORMAL
        and not signals.is_suspicious
    ):
        raise AssertionError(
            "Safety violation: a "
            f"{classification.priority.value} email was routed to Review "
            f"({classification.message_id})."
        )


def classify_all(
    messages: list[EmailMessage], context: ClassificationContext | None = None
) -> list[Classification]:
    return [classify(message, context) for message in messages]


__all__ = (
    "CONFIDENCE_EXPLICIT_RULE",
    "CONFIDENCE_FALLBACK",
    "CONFIDENCE_STRONG_TOPIC",
    "CONFIDENCE_STRUCTURAL",
    "CONFIDENCE_UNRESOLVED",
    "Classification",
    "classify",
    "classify_all",
)
