"""Human-in-the-loop learning (CLAUDE.md §11).

Three things live here:

1. **Feedback recording** — every Review-queue action writes a
   ``Review_Feedback`` row, whether or not it also produces a suggestion.
2. **Suggestions** — ``propose_sender_rule`` / ``propose_domain_rule`` /
   ``suggest_vip`` turn one dashboard click into a *pending* row in
   ``Learned_Rule_Suggestions`` or ``VIPs``. None of them ever create an
   active rule. A domain rule aimed at a public mailbox provider (gmail.com,
   outlook.com, ...) is refused outright — approving one address there must
   never be read as trusting everyone who uses it (CLAUDE.md §8).
3. **Correspondence-pattern VIP suggestions** — ``suggest_vips_from_results``
   looks at a batch of already-classified mail for the signals CLAUDE.md §8
   names (frequent correspondence, replies/active threads, stars) and
   proposes VIPs from that, still always as a suggestion.
4. **Promotion** — ``promote_approved_suggestions`` is the one place a
   suggestion becomes a real rule, and only for suggestions the user has
   already flipped to ``approved`` by hand in the control workbook. It is
   never called automatically from a read-only page load or a classification
   run — only from the explicit ``POST /learning/promote-suggestions`` route,
   so nothing here ever surprises the user.
"""

from __future__ import annotations

from app.audit.models import safe_subject_ref
from app.learning.models import (
    DECISION_DOMAIN_RULE_SUGGESTED,
    DECISION_KEPT,
    DECISION_REVIEW_CORRECT,
    DECISION_SENDER_RULE_SUGGESTED,
    DECISION_VIP_SUGGESTED,
    VIP_FREQUENCY_THRESHOLD,
    FeedbackOutcome,
)
from app.logging_config import get_logger

log = get_logger("app.learning.service")

__all__ = (
    "keep",
    "make_domain_rule",
    "make_sender_rule",
    "promote_approved_suggestions",
    "record_review_feedback",
    "review_correct",
    "suggest_vip",
    "suggest_vips_from_results",
)


def record_review_feedback(
    workbook,
    *,
    message_id: str,
    thread_id: str,
    original_classification: str,
    original_reason: str,
    user_decision: str,
    resulting_rule_suggestion: str = "",
) -> None:
    workbook.review_feedback.record(
        gmail_message_id=message_id,
        thread_id=thread_id,
        original_classification=original_classification,
        original_reason=original_reason,
        user_decision=user_decision,
        resulting_rule_suggestion=resulting_rule_suggestion,
    )


# --------------------------------------------------------------------
# The five active Review-queue actions
# --------------------------------------------------------------------


def keep(workbook, *, message_id: str, thread_id: str, classification: str, reason: str) -> FeedbackOutcome:
    """A light, neutral signal: "I saw this, leave it as it is."

    Deliberately does **not** feed rule suggestions by itself — it is the
    no-strong-opinion option, distinct from :func:`review_correct` below
    (CLAUDE.md §11: behavioral signals must not silently create rules).
    """
    record_review_feedback(
        workbook,
        message_id=message_id,
        thread_id=thread_id,
        original_classification=classification,
        original_reason=reason,
        user_decision=DECISION_KEPT,
    )
    return FeedbackOutcome(ok=True, message="Noted — left as is.")


def review_correct(workbook, *, message_id: str, thread_id: str, classification: str, reason: str) -> FeedbackOutcome:
    """An explicit confirmation that Review was the right call for this message."""
    record_review_feedback(
        workbook,
        message_id=message_id,
        thread_id=thread_id,
        original_classification=classification,
        original_reason=reason,
        user_decision=DECISION_REVIEW_CORRECT,
    )
    return FeedbackOutcome(ok=True, message="Thanks — confirmed as correctly reviewed.")


def make_sender_rule(
    workbook, *, message_id: str, thread_id: str, sender_email: str, subject: str, classification: str, reason: str
) -> FeedbackOutcome:
    sender_email = (sender_email or "").strip().lower()
    if "@" not in sender_email:
        return FeedbackOutcome(ok=False, message="No sender address to build a rule from.")

    evidence = (
        f'You asked to always keep mail from {sender_email} out of AI/Review, '
        f'from the message "{safe_subject_ref(subject)}".'
    )
    suggestion_id = workbook.rules.add_rule_suggestion(
        target=sender_email,
        suggested_rule="whitelist — keep this sender out of AI/Review",
        evidence=evidence,
        confidence=1.0,
    )
    record_review_feedback(
        workbook,
        message_id=message_id,
        thread_id=thread_id,
        original_classification=classification,
        original_reason=reason,
        user_decision=DECISION_SENDER_RULE_SUGGESTED,
        resulting_rule_suggestion=suggestion_id,
    )
    return FeedbackOutcome(
        ok=True,
        message=(
            f"Suggested always keeping mail from {sender_email} out of Review. "
            "It won't take effect until you approve it in the control workbook."
        ),
        suggestion_id=suggestion_id,
    )


def make_domain_rule(
    workbook, *, message_id: str, thread_id: str, sender_email: str, subject: str, classification: str, reason: str
) -> FeedbackOutcome:
    from app.classification.message import domain_of
    from app.classification.patterns import PUBLIC_EMAIL_PROVIDERS

    domain = domain_of(sender_email)
    if not domain:
        return FeedbackOutcome(ok=False, message="No sender domain to build a rule from.")

    if domain in PUBLIC_EMAIL_PROVIDERS:
        return FeedbackOutcome(
            ok=False,
            message=(
                f'"{domain}" is a public email provider, so the app won\'t create a '
                "domain-wide rule from it — approving one address there doesn't mean "
                'trusting everyone who uses it. Use "Make Sender Rule" for just this '
                "address instead."
            ),
        )

    evidence = (
        f'You asked to always keep mail from @{domain} out of AI/Review, '
        f'from the message "{safe_subject_ref(subject)}".'
    )
    suggestion_id = workbook.rules.add_rule_suggestion(
        target=domain,
        suggested_rule="whitelist domain — keep this domain out of AI/Review",
        evidence=evidence,
        confidence=1.0,
    )
    record_review_feedback(
        workbook,
        message_id=message_id,
        thread_id=thread_id,
        original_classification=classification,
        original_reason=reason,
        user_decision=DECISION_DOMAIN_RULE_SUGGESTED,
        resulting_rule_suggestion=suggestion_id,
    )
    return FeedbackOutcome(
        ok=True,
        message=(
            f"Suggested always keeping mail from @{domain} out of Review. "
            "It won't take effect until you approve it in the control workbook."
        ),
        suggestion_id=suggestion_id,
    )


def suggest_vip(
    workbook, *, message_id: str, thread_id: str, sender_email: str, sender_name: str, subject: str,
    classification: str, reason: str,
) -> FeedbackOutcome:
    sender_email = (sender_email or "").strip().lower()
    if "@" not in sender_email:
        return FeedbackOutcome(ok=False, message="No sender address to suggest as a VIP.")

    workbook.vips.suggest(
        sender_email,
        name=sender_name,
        notes=f'suggested from the Review queue: "{safe_subject_ref(subject)}"',
    )
    record_review_feedback(
        workbook,
        message_id=message_id,
        thread_id=thread_id,
        original_classification=classification,
        original_reason=reason,
        user_decision=DECISION_VIP_SUGGESTED,
    )
    return FeedbackOutcome(
        ok=True,
        message=f"Suggested {sender_email} as a VIP. Approve them in the control workbook to protect their mail.",
    )


# --------------------------------------------------------------------
# Correspondence-pattern VIP suggestions (CLAUDE.md §8)
# --------------------------------------------------------------------


def suggest_vips_from_results(workbook, results: list) -> list[str]:
    """Scan one classification batch for correspondence signals and propose VIPs.

    Signals used, all already computed by Phases 3-8 (no extra Gmail calls):
    the sender appears at least :data:`VIP_FREQUENCY_THRESHOLD` times in this
    window, or the user starred one of their messages, or one of their threads
    is an active back-and-forth. A sender only qualifies if at least one of
    their messages was itself classified Personal or Work/Business — the
    signal is about *people*, not newsletters that happen to reappear often.

    Always a suggestion (``VIPRepository.suggest`` is pending-only and
    idempotent) — never a silent promotion (CLAUDE.md §8).
    """
    from app.classification.labels import Label

    already_vip = workbook.vips.approved_emails()
    already_suggested = {v.email for v in workbook.vips.suggested()}

    by_sender: dict[str, list] = {}
    for result in results:
        message = result.message
        if message.sent_by_user or not message.sender_email:
            continue
        by_sender.setdefault(message.sender_email, []).append(result)

    suggested: list[str] = []
    for sender_email, group in by_sender.items():
        if sender_email in already_vip or sender_email in already_suggested:
            continue

        looks_personal = any(
            Label.PERSONAL in r.classification.labels or Label.WORK_BUSINESS in r.classification.labels
            for r in group
        )
        if not looks_personal:
            continue

        starred = any(r.message.is_starred for r in group)
        active_thread = any(r.message.is_active_thread for r in group)
        frequent = len(group) >= VIP_FREQUENCY_THRESHOLD
        if not (starred or active_thread or frequent):
            continue

        reasons = []
        if starred:
            reasons.append("you starred a message from them")
        if active_thread:
            reasons.append("an active back-and-forth thread")
        if frequent:
            reasons.append(f"{len(group)} messages in your recent mail")
        sender_name = next((r.message.sender_name for r in group if r.message.sender_name), "")

        workbook.vips.suggest(
            sender_email,
            name=sender_name,
            notes="Suggested because: " + "; ".join(reasons),
        )
        suggested.append(sender_email)

    log.info("vip_suggestions_from_correspondence", extra={"count": len(suggested)})
    return suggested


# --------------------------------------------------------------------
# Promotion — the one place a suggestion becomes a real rule
# --------------------------------------------------------------------


def promote_approved_suggestions(workbook) -> list[str]:
    """Turn every *approved* Learned_Rule_Suggestions row into an active rule.

    A suggestion only reaches this function's attention after the user has
    changed its ``status`` to ``approved`` by hand in the control workbook —
    this is pure mechanical follow-through on a decision already made, not a
    new decision (CLAUDE.md §11: never silently create a permanent rule).
    Idempotent: re-running it just updates the same Sender_Rules/Domain_Rules
    row rather than duplicating it.
    """
    from app.classification.patterns import PUBLIC_EMAIL_PROVIDERS

    promoted: list[str] = []
    for row in workbook.rules.approved_suggestions():
        target = (row.get("target") or "").strip().lower()
        if not target:
            continue
        notes = f"promoted from suggestion {row.get('suggestion_id')}"
        if "@" in target:
            workbook.rules.add_sender_rule(target, source="learned", notes=notes)
            promoted.append(target)
        elif target.lstrip("@") in PUBLIC_EMAIL_PROVIDERS:
            log.warning("domain_promotion_refused_public_provider", extra={"target": target})
        else:
            workbook.rules.add_domain_rule(target, source="learned", notes=notes)
            promoted.append(target)

    log.info("suggestions_promoted", extra={"count": len(promoted)})
    return promoted
