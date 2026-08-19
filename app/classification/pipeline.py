"""Wiring between live Google data and the pure rules engine.

The engine itself knows nothing about Gmail, Sheets or Contacts. This module
is the only place that fetches from all three, assembles a
:class:`~app.classification.context.ClassificationContext`, and runs the
classifier over real mail.

Everything here is **read-only**. Nothing in this module — or anything it calls
— can modify a message. Applying decisions to Gmail is Phase 11.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.classification.context import ClassificationContext, context_from_workbook
from app.classification.engine import Classification, classify
from app.classification.message import EmailMessage, from_gmail
from app.google_api import NotConnectedError
from app.logging_config import get_logger

log = get_logger("app.classification.pipeline")

#: Hard ceiling on a preview run, so a stray query can't sweep the whole mailbox.
MAX_PREVIEW_MESSAGES = 50


@dataclass
class PreviewResult:
    """One message and what the engine decided about it."""

    message: EmailMessage
    classification: Classification
    #: Present only when the AI step ran for this message (Phase 4).
    ai: object | None = None
    #: Present only when the message had attachments (Phase 5).
    attachments: object | None = None
    #: Per-message extracted intelligence (Phase 6). Set by build_intelligence.
    intelligence: object | None = None

    def as_dict(self) -> dict[str, object]:
        """A JSON-safe view. Deliberately excludes the message body."""
        message, decision = self.message, self.classification
        view: dict[str, object] = {
            "id": message.message_id,
            "thread_id": message.thread_id,
            "from": message.sender_email,
            "from_name": message.sender_name,
            "subject": message.subject,
            "date": message.date.isoformat() if message.date else None,
            "has_attachments": message.has_attachments,
            "labels": decision.gmail_label_names,
            "priority": decision.priority.value,
            "action_required": decision.action_required,
            "would_keep_in_inbox": decision.keep_in_inbox,
            "would_archive": decision.archive,
            "would_mark_important": decision.mark_important,
            "would_review": decision.review,
            "review_reason": decision.review_reason,
            "protected": decision.protected,
            "protection_reasons": list(decision.protection_reasons),
            "confidence": decision.confidence,
            "needs_ai": decision.needs_ai,
            "why": decision.rationale,
            "rules_triggered": list(decision.rules_triggered),
        }
        if self.attachments is not None:
            view["attachments"] = self.attachments.as_dict()  # type: ignore[attr-defined]
        if self.ai is not None:
            view["ai"] = self.ai.as_dict()  # type: ignore[attr-defined]
        if self.intelligence is not None:
            view["intelligence"] = self.intelligence.as_dict()  # type: ignore[attr-defined]
        return view


def build_live_context(
    include_contacts: bool = True,
    include_workbook: bool = True,
    user_email: str = "",
    workbook=None,
) -> ClassificationContext:
    """Assemble a context from the workbook and Google Contacts.

    Both sources are optional and failures are non-fatal: a missing workbook or
    a Contacts error degrades the context rather than breaking classification.
    That's the safe direction — a thinner context means *less* is eligible for
    Review, never more.

    ``workbook`` lets a caller that has already connected one (e.g. the Phase
    13 real-time poller, which needs it for the history cursor and audit rows
    regardless) pass it straight through instead of this function opening a
    second, redundant connection.
    """
    known_contacts: set[str] = set()
    if include_contacts:
        try:
            from app.gmail.people import get_client as get_people_client

            known_contacts = get_people_client().all_known_emails()
        except NotConnectedError:
            raise
        except Exception as exc:  # noqa: BLE001 — degrade, don't fail
            log.warning("contacts_lookup_failed", extra={"error": str(exc)})

    if not include_workbook:
        return ClassificationContext(
            user_email=user_email, known_contacts=known_contacts
        )

    try:
        if workbook is None:
            from app.sheets.repository import ControlWorkbook

            workbook = ControlWorkbook.connect()
        return context_from_workbook(
            workbook,
            user_email=user_email,
            known_contacts=known_contacts,
            # Everyone in Contacts has been corresponded with at least once;
            # true reply history arrives with the audit log in Phase 9.
            prior_correspondents=known_contacts,
        )
    except NotConnectedError:
        raise
    except Exception as exc:  # noqa: BLE001 — degrade, don't fail
        log.warning("workbook_rules_unavailable", extra={"error": str(exc)})
        return ClassificationContext(
            user_email=user_email,
            known_contacts=known_contacts,
            prior_correspondents=known_contacts,
        )


def classify_raw_messages(
    raw_messages: list[dict],
    user_email: str,
    context: ClassificationContext,
    gmail,
    use_ai: bool = True,
    read_attachments: bool = True,
    provider=None,
    tracker=None,
) -> list[PreviewResult]:
    """Classify already-fetched raw Gmail message resources.

    This is the shared core of :func:`preview_recent` — split out so a caller
    that has already assembled its own message list doesn't have to re-fetch
    through a single ``list_recent_messages`` call, and doesn't run into
    :data:`MAX_PREVIEW_MESSAGES`. That cap exists to bound one ad-hoc query
    from sweeping the whole mailbox; it has nothing useful to say about a
    deliberately assembled, already-bounded batch such as Phase 10's
    stratified 250-email acceptance sample (several bounded searches
    combined, not one big query).

    Attachments are read first, so their contents can inform the rules. The AI
    step runs only for messages the deterministic rules flagged ``needs_ai``;
    everything else costs nothing.
    """
    from app.ai import assist
    from app.attachments import process_message

    results: list[PreviewResult] = []
    for raw in raw_messages:
        message = from_gmail(raw, user_email=user_email)

        # Read attachments before classifying, so their contents can inform
        # the rules. A failure here is inert by design — it leaves the message
        # exactly as it would have been.
        report = None
        if read_attachments and message.has_attachments:
            report = process_message(message, gmail_client=gmail)

        decision = classify(message, context)
        outcome = None

        if use_ai and provider is not None and decision.needs_ai:
            outcome = assist(message, decision, provider, tracker=tracker)
            decision = outcome.classification

        results.append(
            PreviewResult(
                message=message,
                classification=decision,
                ai=outcome,
                attachments=report,
            )
        )
    return results


def preview_recent(
    limit: int = 10,
    query: str | None = None,
    include_contacts: bool = True,
    include_workbook: bool = True,
    use_ai: bool = True,
    read_attachments: bool = True,
    provider=None,
    tracker=None,
) -> list[PreviewResult]:
    """Classify the most recent messages without changing anything."""
    from app.ai import build_provider
    from app.ai.costs import CostTracker
    from app.gmail.client import get_client as get_gmail_client

    limit = max(1, min(limit, MAX_PREVIEW_MESSAGES))
    gmail = get_gmail_client()

    profile = gmail.get_profile()
    user_email = str(profile.get("emailAddress") or "").lower()

    context = build_live_context(
        include_contacts=include_contacts,
        include_workbook=include_workbook,
        user_email=user_email,
    )

    if use_ai and provider is None:
        provider = build_provider()
    tracker = tracker if tracker is not None else CostTracker()

    raw_messages = gmail.list_recent_messages(max_results=limit, query=query)
    results = classify_raw_messages(
        raw_messages,
        user_email,
        context,
        gmail,
        use_ai=use_ai,
        read_attachments=read_attachments,
        provider=provider,
        tracker=tracker,
    )

    log.info(
        "classification_preview_completed",
        extra={
            "count": len(results),
            "would_review": sum(r.classification.review for r in results),
            "protected": sum(r.classification.protected for r in results),
            "ai_calls": tracker.call_count,
        },
    )
    return results


def build_intelligence(results: list[PreviewResult], today=None):
    """Run the Phase 6 intelligence pass over already-classified results.

    Attaches each message's per-message intelligence to its ``PreviewResult``
    (for display) and returns the full :class:`IntelligenceReport`, whose
    ``batch`` holds the cross-message groupings (trips, orders, duplicates).

    Read-only: this extracts and returns facts. It writes nothing to Gmail and
    nothing to the workbook — persistence is a separate, explicit call.
    """
    from app.intelligence import analyze

    messages = [r.message for r in results]
    classifications = {r.message.message_id: r.classification for r in results}
    report = analyze(messages, classifications=classifications, today=today)

    for result in results:
        result.intelligence = report.for_message(result.message.message_id)
    return report


def summarize(results: list[PreviewResult]) -> dict[str, object]:
    """Aggregate counts for the preview page."""
    total = len(results)
    decisions = [r.classification for r in results]
    consulted = [r for r in results if r.ai is not None and r.ai.ai_was_called]  # type: ignore[attr-defined]
    with_attachments = [r for r in results if r.attachments is not None]
    return {
        "total": total,
        "ai_consulted": len(consulted),
        "with_attachments": len(with_attachments),
        "attachments_unreadable": sum(
            1 for r in with_attachments if r.attachments.any_failed  # type: ignore[attr-defined]
        ),
        "attachments_blocked": sum(
            1 for r in with_attachments if r.attachments.any_dangerous  # type: ignore[attr-defined]
        ),
        "protected": sum(d.protected for d in decisions),
        "would_review": sum(d.review for d in decisions),
        "would_keep_in_inbox": sum(d.keep_in_inbox for d in decisions),
        "would_archive": sum(d.archive for d in decisions),
        "action_required": sum(d.action_required for d in decisions),
        "needs_ai": sum(d.needs_ai for d in decisions),
        "by_priority": {
            priority: sum(d.priority.value == priority for d in decisions)
            for priority in ("P1", "P2", "P3")
        },
        # The launch-gate metric from CLAUDE.md §15.
        "protected_routed_to_review": sum(d.protected and d.review for d in decisions),
    }


__all__ = (
    "MAX_PREVIEW_MESSAGES",
    "PreviewResult",
    "build_intelligence",
    "build_live_context",
    "classify_raw_messages",
    "preview_recent",
    "summarize",
)
