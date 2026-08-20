"""Gmail write-capable client (Phase 11).

Deliberately a *separate* class from :class:`app.gmail.client.GmailReadClient`
— that client has a test asserting it never grows a method that looks like a
mutation. Everything that can change Gmail lives here instead, behind the
``gmail.modify`` scope (CLAUDE.md §5):

* Add/remove the taxonomy's labels (creating them in Gmail on first use).
* Archive (remove ``INBOX``) / restore to Inbox (add ``INBOX``).
* Mark Important (add ``IMPORTANT``) — never unmark it automatically; taking
  away a signal the user or Gmail's own ML set is not this app's call to make.
* Move to Gmail's Trash / undo that — recoverable for 30 days, never a
  permanent delete. There is no method here for a permanent delete because
  the ``gmail.modify`` scope this client is built from does not grant one.

Every method that changes a message issues exactly one ``messages.modify``
call, combining every label add/remove into one request — the same lesson
Phase 10 learned the hard way about Sheets: don't turn one message into
several API calls when one will do.
"""

from __future__ import annotations

from typing import Any

from googleapiclient.discovery import Resource

from app.classification.labels import Label
from app.google_api import build_service, load_stored_token_or_raise
from app.logging_config import get_logger

log = get_logger("app.gmail.write_client")

_GMAIL_API = ("gmail", "v1")

#: Gmail system labels this app is allowed to touch. Nothing here is a
#: destructive action — Trash is a separate, explicit method, never implied
#: by a label change.
INBOX_LABEL = "INBOX"
IMPORTANT_LABEL = "IMPORTANT"
#: The label Gmail itself adds when a message is trashed — read-only signal
#: for callers (Phase 12's Undo) that want to detect a Trash action from
#: label state alone. Never written directly; only ``trash_message`` /
#: ``untrash_message`` change it, via Gmail's own API behavior.
TRASH_LABEL = "TRASH"

#: One (background, text) color per taxonomy label, so it's visually
#: distinguishable in Gmail's sidebar and message list -- not just a wall of
#: same-colored tags. Both values must come from Gmail's own fixed
#: palette (``users.labels`` API's ``LabelColor`` schema); arbitrary hex
#: values are rejected with a 400. Colors group loosely by urgency/meaning:
#: red/orange for things needing attention, blue for people/work, green for
#: money, gray for low-value/expired, purple for reference material.
LABEL_COLORS: dict[str, dict[str, str]] = {
    Label.CRITICAL.value: {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"},
    Label.ACTION_REQUIRED.value: {"backgroundColor": "#ffad47", "textColor": "#000000"},
    Label.PERSONAL.value: {"backgroundColor": "#a4c2f4", "textColor": "#1c4587"},
    Label.WORK_BUSINESS.value: {"backgroundColor": "#4a86e8", "textColor": "#ffffff"},
    Label.PURCHASES_RECEIPTS.value: {"backgroundColor": "#16a766", "textColor": "#ffffff"},
    Label.NEWSLETTER.value: {"backgroundColor": "#a479e2", "textColor": "#ffffff"},
    Label.LOW_VALUE.value: {"backgroundColor": "#999999", "textColor": "#ffffff"},
    Label.REVIEW.value: {"backgroundColor": "#fad165", "textColor": "#000000"},
    Label.EDUCATION.value: {"backgroundColor": "#43d692", "textColor": "#000000"},
    Label.SECURITY.value: {"backgroundColor": "#cc3a21", "textColor": "#ffffff"},
    Label.FINANCIAL.value: {"backgroundColor": "#149e60", "textColor": "#ffffff"},
    Label.CAREER.value: {"backgroundColor": "#3c78d8", "textColor": "#ffffff"},
    Label.SUSPICIOUS.value: {"backgroundColor": "#822111", "textColor": "#ffffff"},
    Label.IMPORTANT_DOCUMENT.value: {"backgroundColor": "#285bac", "textColor": "#ffffff"},
    Label.SUBSCRIPTION_REVIEW.value: {"backgroundColor": "#8e63ce", "textColor": "#ffffff"},
    Label.EXPIRED.value: {"backgroundColor": "#666666", "textColor": "#ffffff"},
}


class GmailWriteClient:
    """Wraps the Gmail API calls that change a message's state.

    Callers should generally use :func:`get_write_client`, which handles the
    stored credentials the same way :func:`app.gmail.client.get_client` does.
    """

    def __init__(self, service: Resource) -> None:
        self._service = service
        #: name -> id, populated lazily and reused for this client's life —
        #: labels don't change mid-run, so there's no reason to re-list them
        #: before every message the way the Sheets header bug used to.
        self._label_ids: dict[str, str] | None = None

    # -------- Label discovery / creation --------

    def _load_labels(self) -> dict[str, str]:
        if self._label_ids is None:
            result = self._service.users().labels().list(userId="me").execute()
            self._label_ids = {
                label["name"]: label["id"] for label in result.get("labels") or []
            }
        return self._label_ids

    def label_names(self) -> set[str]:
        """Every label name that already exists in Gmail -- including ones
        the user made by hand. Used by
        :func:`app.gmail.vendor_labels.match_existing_label` to recognize a
        folder like "Uber" the user already has, without ever creating one.
        """
        return set(self._load_labels())

    def ensure_labels(self, names: list[str]) -> dict[str, str]:
        """Return ``{name: label_id}`` for every name, creating any that are
        missing in Gmail. Gmail treats ``/`` in a label name as a nested-label
        separator on its own — creating ``Parent/Child`` directly is enough,
        no separate parent-label step is needed.

        A newly created label gets its :data:`LABEL_COLORS` entry immediately,
        if it has one, so the taxonomy is color-coded from the moment each
        label first appears rather than needing a separate backfill step.
        """
        known = self._load_labels()
        missing = [name for name in names if name not in known]
        for name in missing:
            body: dict[str, Any] = {
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            }
            color = LABEL_COLORS.get(name)
            if color:
                body["color"] = color
            created = (
                self._service.users()
                .labels()
                .create(userId="me", body=body)
                .execute()
            )
            known[name] = created["id"]
            log.info("gmail_label_created", extra={"label_name": name})
        return {name: known[name] for name in names}

    def sync_label_colors(self) -> dict[str, str]:
        """Apply :data:`LABEL_COLORS` to every already-existing taxonomy
        label, for labels created before a color was assigned (or before this
        method existed at all). Returns ``{label_name: outcome}`` for
        reporting; never creates a label that doesn't already exist --
        that's :meth:`ensure_labels`'s job.
        """
        known = self._load_labels()
        outcomes: dict[str, str] = {}
        for name, color in LABEL_COLORS.items():
            label_id = known.get(name)
            if label_id is None:
                outcomes[name] = "not created yet"
                continue
            self._service.users().labels().patch(
                userId="me", id=label_id, body={"color": color}
            ).execute()
            outcomes[name] = "colored"
        return outcomes

    # -------- Message modification --------

    def modify_message(
        self,
        message_id: str,
        *,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply one combined label add/remove to one message.

        A no-op (both lists empty) still executes — callers are expected to
        skip the call entirely when there's nothing to change; see
        :mod:`app.gmail.apply` for that idempotency check.
        """
        body: dict[str, list[str]] = {}
        if add_label_ids:
            body["addLabelIds"] = list(add_label_ids)
        if remove_label_ids:
            body["removeLabelIds"] = list(remove_label_ids)
        return (
            self._service.users()
            .messages()
            .modify(userId="me", id=message_id, body=body)
            .execute()
        )

    def trash_message(self, message_id: str) -> dict[str, Any]:
        """Move to Gmail's Trash. Recoverable for 30 days — never permanent."""
        return (
            self._service.users()
            .messages()
            .trash(userId="me", id=message_id)
            .execute()
        )

    def untrash_message(self, message_id: str) -> dict[str, Any]:
        """Restore a message out of Trash (used by Undo Last Run, Phase 12)."""
        return (
            self._service.users()
            .messages()
            .untrash(userId="me", id=message_id)
            .execute()
        )


def get_write_client() -> GmailWriteClient:
    """Load the stored token and return a ready-to-use write client.

    Raises ``NotConnectedError`` if no token is stored, or if the stored
    token predates Phase 11 and is missing the ``gmail.modify`` scope — the
    caller must send the user through ``/oauth/start`` again in that case.
    """
    stored = load_stored_token_or_raise()
    return GmailWriteClient(service=build_service(*_GMAIL_API, stored=stored))


__all__ = (
    "GmailWriteClient",
    "IMPORTANT_LABEL",
    "INBOX_LABEL",
    "LABEL_COLORS",
    "TRASH_LABEL",
    "get_write_client",
)
