"""Gmail change detection via the history API (CLAUDE.md §13).

Polling instead of push notifications is a deliberate product decision (see
``docs/plain-english/PHASE_13_REALTIME_PROCESSING.md``): no Google Cloud
Pub/Sub topic, no public webhook to secure, no domain verification, and it
works unchanged on the single Render web service this app already deploys
as. The trade-off is that new mail is noticed on the next poll rather than
the instant it lands — an acceptable "near" in "near-real-time" for a
single-user personal agent.

``users.history.list`` is a change *feed*, not a snapshot: given a history id
the app last saw, Gmail returns only what changed since then. A poll cycle
therefore costs roughly one API call, not "re-list recent messages and diff
them by hand" every couple of minutes.
"""

from __future__ import annotations

from dataclasses import dataclass

from googleapiclient.errors import HttpError

from app.gmail.client import GmailReadClient
from app.logging_config import get_logger
from app.scheduling.retry import call_with_retry

log = get_logger("app.scheduling.history")

#: Only new messages trigger processing (CLAUDE.md §13's "new-email
#: triggering"). Deliberately excludes labelAdded/labelRemoved: reacting to
#: label changes would mean reacting to this app's *own* prior writes (an
#: added taxonomy label is itself a labelAdded history record) — plan_change's
#: idempotency would keep that harmless, but there is no reason to pay for
#: the extra fetch/classify cycle on every one of this app's own actions.
HISTORY_TYPES = ["messageAdded"]


@dataclass(frozen=True)
class ChangedMessage:
    """One new message, as reported by the history feed."""

    message_id: str
    thread_id: str


@dataclass(frozen=True)
class HistoryScan:
    """What changed since the last poll, and where the cursor is now."""

    messages: tuple[ChangedMessage, ...]
    new_history_id: str
    #: True when Gmail could no longer answer from the stored cursor (it had
    #: expired) and the cursor was reset to "now" — any mail that arrived
    #: during the gap was not seen by this scan.
    history_gap: bool = False


def current_history_id(gmail: GmailReadClient) -> str:
    """The mailbox's current history id — the baseline a fresh cursor starts
    from. A cheap profile call; no message data is read."""
    profile = gmail.get_profile()
    return str(profile.get("historyId") or "")


def scan_for_changes(gmail: GmailReadClient, start_history_id: str) -> HistoryScan:
    """Return every new message since ``start_history_id``, paginating as
    needed, plus the history id to store as the next cursor.

    If ``start_history_id`` has expired (Gmail only retains history for a
    limited window), Gmail answers with a 404. That is treated as "the gap is
    unrecoverable from this feed" rather than an error to raise: the cursor
    resets to the mailbox's current history id and the caller gets
    ``history_gap=True`` so it can log the fact plainly, rather than the poll
    loop crashing or silently pretending nothing happened during the gap. A
    real catch-up over that period is /gmail/apply, /acceptance/run, or the
    future Phase 15 historical pass's job — not this feed's.
    """
    changed: dict[str, ChangedMessage] = {}
    page_token: str | None = None
    latest_history_id = start_history_id

    while True:
        current_token = page_token

        def _fetch_page() -> dict:
            return gmail.list_history(
                start_history_id,
                history_types=HISTORY_TYPES,
                page_token=current_token,
            )

        try:
            page = call_with_retry(_fetch_page, description="history.list")
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                log.warning(
                    "history_cursor_expired",
                    extra={"start_history_id": start_history_id},
                )
                return HistoryScan(
                    messages=(),
                    new_history_id=current_history_id(gmail),
                    history_gap=True,
                )
            raise

        for record in page.get("history") or []:
            for added in record.get("messagesAdded") or []:
                msg = added.get("message") or {}
                message_id = str(msg.get("id") or "")
                thread_id = str(msg.get("threadId") or "")
                if message_id:
                    changed[message_id] = ChangedMessage(message_id, thread_id)

        if page.get("historyId"):
            latest_history_id = str(page["historyId"])

        page_token = page.get("nextPageToken")
        if not page_token:
            break

    return HistoryScan(messages=tuple(changed.values()), new_history_id=latest_history_id)


__all__ = (
    "HISTORY_TYPES",
    "ChangedMessage",
    "HistoryScan",
    "current_history_id",
    "scan_for_changes",
)
