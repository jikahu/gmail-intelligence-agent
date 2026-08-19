"""Turn everything Phases 3-7 compute into one Command Center view (§13).

The dashboard is mostly a *view*: the hard work already exists. This module
runs the read-only pipeline once, then arranges the results into the nine cards
the Command Center shows and the row lists behind them. It changes nothing —
not Gmail, not the workbook.

Design notes:

* **Read-only and AI-free by default.** The dashboard renders from the
  deterministic engine, so opening a page never spends money or touches a
  mailbox. (AI second opinions are a per-scan thing, not a page-load thing.)
* **One computation, many cards.** We build every row list once and hang them
  off :class:`CommandCenter`, so clicking a card is a filter, not a re-run.
* **Attachment *text* isn't read here.** The attachment indicator comes from
  metadata; downloading file contents for a dashboard render would be wasteful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from app.logging_config import get_logger

log = get_logger("app.dashboard.service")

#: How many recent messages the dashboard reasons over in one render.
DEFAULT_WINDOW = 50


@dataclass
class Card:
    """One clickable Command Center tile."""

    key: str
    title: str
    count: int
    blurb: str
    tone: str  # critical / important / info / muted


@dataclass
class Row:
    """A single line in a list view. Everything here is display-safe data.

    The text fields originate in untrusted email, so the *view* HTML-escapes
    them — this layer keeps them raw.
    """

    message_id: str
    thread_id: str
    sender_email: str
    sender_name: str
    subject: str
    received: Optional[str]  # ISO datetime or None
    #: Gmail's own one-line preview of the message body — what it's actually
    #: about. Distinct from ``reason`` (why the app flagged it that way);
    #: CLAUDE.md §13 asks for both as separate fields on a dashboard row.
    snippet: str
    reason: str
    confidence: Optional[float]
    labels: list[str]
    has_attachments: bool
    priority: str
    note: str = ""

    @property
    def gmail_url(self) -> str:
        """A direct link to the real message in Gmail, for the one time a
        one-line summary genuinely isn't enough and the user wants to read
        the whole thing. Empty when there's no real message behind the row
        (e.g. a VIP suggestion)."""
        if not self.message_id:
            return ""
        return f"https://mail.google.com/mail/u/0/#all/{self.message_id}"


#: Card order and copy, straight from CLAUDE.md §13.
CARD_DEFS: tuple[tuple[str, str, str, str], ...] = (
    ("p1", "P1 Urgent", "Urgent — needs your attention today.", "critical"),
    ("p2", "P2 Important", "Important — deal with these soon.", "important"),
    ("action", "Action Required", "These need a reply or an action from you.", "important"),
    ("waiting", "Waiting for Reply", "You've waited 3 business days for a reply.", "info"),
    ("due_soon", "Due Soon", "A deadline within the next 3 business days.", "info"),
    ("overdue", "Overdue", "Past a deadline, or unactioned for 3 business days.", "critical"),
    ("review", "AI Review", "Low-value or uncertain mail set aside — never deleted.", "muted"),
    ("vip", "VIP Suggestions", "People you email a lot. Approve to always protect them.", "muted"),
    ("subscriptions", "Subscription Review", "Recurring charges you might not need. Never auto-cancelled.", "muted"),
)

CARD_KEYS: tuple[str, ...] = tuple(key for key, *_ in CARD_DEFS)
_CARD_META: dict[str, tuple[str, str, str]] = {
    key: (title, blurb, tone) for key, title, blurb, tone in CARD_DEFS
}


@dataclass
class CommandCenter:
    """The whole dashboard for one render."""

    account: str
    generated_at: datetime
    dry_run: bool
    cards: list[Card]
    lists: dict[str, list[Row]] = field(default_factory=dict)

    def card(self, key: str) -> Card | None:
        for card in self.cards:
            if card.key == key:
                return card
        return None

    def rows(self, key: str) -> list[Row]:
        return self.lists.get(key, [])

    @property
    def total(self) -> int:
        return sum(card.count for card in self.cards)


# --------------------------------------------------------------------
# Row builders
# --------------------------------------------------------------------


def _received(msg) -> str | None:
    return msg.date.isoformat() if (msg is not None and msg.date) else None


def _row_from_result(result) -> Row:
    msg, decision = result.message, result.classification
    return Row(
        message_id=msg.message_id,
        thread_id=msg.thread_id,
        sender_email=msg.sender_email,
        sender_name=msg.sender_name or msg.sender_email,
        subject=msg.subject or "(no subject)",
        received=_received(msg),
        snippet=msg.snippet,
        reason=decision.review_reason or decision.rationale,
        confidence=decision.confidence,
        labels=decision.gmail_label_names,
        has_attachments=msg.has_attachments,
        priority=decision.priority.value,
        note=_intelligence_note(result.intelligence),
    )


def _intelligence_note(intel) -> str:
    if intel is None:
        return ""
    fin = getattr(intel, "financial", None)
    if fin is not None and fin.amount is not None:
        money = f"{fin.currency or ''}{fin.amount:g}".strip()
        if fin.due_date:
            return f"{money} due {fin.due_date}"
        return money
    return ""


def _followup_note(item) -> str:
    if item.due_date:
        return f"deadline {item.due_date}"
    if item.business_days_elapsed is not None and item.since:
        days = item.business_days_elapsed
        return f"{days} business day{'s' if days != 1 else ''} since {item.since}"
    return ""


def _row_from_followup(item, by_id: dict) -> Row:
    result = by_id.get(item.message_id)
    msg = result.message if result else None
    decision = result.classification if result else None
    labels = list(decision.gmail_label_names) if decision else []
    if item.proposed_label and item.proposed_label not in labels:
        labels = [*labels, item.proposed_label]
    return Row(
        message_id=item.message_id,
        thread_id=item.thread_id,
        sender_email=msg.sender_email if msg else "",
        sender_name=(msg.sender_name or msg.sender_email) if msg else "",
        subject=item.subject or (msg.subject if msg else "(no subject)"),
        received=_received(msg),
        snippet=msg.snippet if msg else "",
        reason=item.reason,
        confidence=decision.confidence if decision else None,
        labels=labels,
        has_attachments=msg.has_attachments if msg else False,
        priority=decision.priority.value if decision else "",
        note=_followup_note(item),
    )


def _row_from_subscription(sub, by_id: dict) -> Row:
    result = by_id.get(sub.message_id)
    msg = result.message if result else None
    note = f"renews {sub.billing_frequency or 'unknown'}"
    if sub.renewal_date:
        note += f" · next {sub.renewal_date}"
    if sub.amount is not None:
        note = f"{sub.currency or ''}{sub.amount:g} · {note}"
    return Row(
        message_id=sub.message_id,
        thread_id=sub.thread_id,
        sender_email=msg.sender_email if msg else sub.sender_domain,
        sender_name=(msg.sender_name or msg.sender_email) if msg else sub.service,
        subject=sub.service or sub.sender_domain,
        received=_received(msg),
        snippet=(msg.snippet if msg else "")
        or "A recurring charge worth a look — the app never cancels it for you.",
        reason="flagged for review (trial ending or price rise)",
        confidence=None,
        labels=["AI/Subscription-Review"],
        has_attachments=False,
        priority="",
        note=note,
    )


def _row_from_vip(vip) -> Row:
    return Row(
        message_id="",
        thread_id="",
        sender_email=vip.email,
        sender_name=vip.name or vip.email,
        subject="Suggested VIP",
        received=None,
        snippet=vip.notes or "Frequent correspondent — approve to protect their mail from Review.",
        reason="suggested from your correspondence patterns",
        confidence=None,
        labels=[],
        has_attachments=False,
        priority="",
        note="awaiting your approval",
    )


# --------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------


def _account_email() -> str:
    from app.gmail.tokens import load_token

    stored = load_token()
    return (stored.account_email or "") if stored is not None else ""


def _load_vip_suggestions() -> list:
    """Read pending VIP suggestions from the workbook, degrading to none.

    Suggestions are generated by the Phase 9 learning layer; before then this
    is normally empty. A workbook that's missing or unreachable is not an error
    for the dashboard — it just means no suggestions to show.
    """
    try:
        from app.sheets.repository import ControlWorkbook

        return ControlWorkbook.connect().vips.suggested()
    except Exception as exc:  # noqa: BLE001 — degrade, don't fail a page load
        log.info("vip_suggestions_unavailable", extra={"error": str(exc)})
        return []


def build_command_center(
    limit: int = DEFAULT_WINDOW,
    query: str | None = None,
    today: date | None = None,
) -> CommandCenter:
    """Compute the full Command Center from recent mail. Read-only."""
    from app.classification import pipeline
    from app.config import get_settings
    from app.followup import service as followup_service

    today = today or date.today()

    results = pipeline.preview_recent(
        limit=limit, query=query, use_ai=False, read_attachments=False
    )
    report = pipeline.build_intelligence(results, today=today)
    followup_service.refine_report(report, today)
    followups = followup_service.evaluate_from_results(results, report, today)

    by_id = {r.message.message_id: r for r in results}

    subscription_reviews = [
        sub
        for sub in report.all_subscriptions()
        if sub.review_status == "suggested_review"
    ]
    vip_suggestions = _load_vip_suggestions()

    lists: dict[str, list[Row]] = {
        "p1": [_row_from_result(r) for r in results if r.classification.priority.value == "P1"],
        "p2": [_row_from_result(r) for r in results if r.classification.priority.value == "P2"],
        "action": [_row_from_result(r) for r in results if r.classification.action_required],
        "review": [_row_from_result(r) for r in results if r.classification.review],
        "waiting": [_row_from_followup(i, by_id) for i in followups.waiting_for_reply],
        "due_soon": [_row_from_followup(i, by_id) for i in followups.due_soon],
        "overdue": [
            _row_from_followup(i, by_id)
            for i in (*followups.overdue_actions, *followups.overdue_deadlines)
        ],
        "subscriptions": [_row_from_subscription(s, by_id) for s in subscription_reviews],
        "vip": [_row_from_vip(v) for v in vip_suggestions],
    }

    cards = [
        Card(key=key, title=_CARD_META[key][0], count=len(lists.get(key, [])),
             blurb=_CARD_META[key][1], tone=_CARD_META[key][2])
        for key in CARD_KEYS
    ]

    return CommandCenter(
        account=_account_email(),
        generated_at=datetime.now(),
        dry_run=get_settings().dry_run,
        cards=cards,
        lists=lists,
    )


__all__ = (
    "CARD_DEFS",
    "CARD_KEYS",
    "Card",
    "CommandCenter",
    "DEFAULT_WINDOW",
    "Row",
    "build_command_center",
)
