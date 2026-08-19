"""Gmail read-only client.

Every method here is a *read* against Gmail. There is intentionally no method
that modifies, sends, trashes, or archives — those verbs simply do not exist
in this module. Phase 11 adds a separate write-capable client behind an
explicit safety switch.

The client is built lazily from the stored token so tests can mock the
underlying ``googleapiclient`` service without needing real credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from googleapiclient.discovery import Resource

from app.google_api import build_service, load_stored_token_or_raise
from app.logging_config import get_logger

log = get_logger("app.gmail.client")

_GMAIL_API = ("gmail", "v1")


@dataclass
class MessageSummary:
    """Header-only view of a Gmail message."""

    id: str
    thread_id: str
    sender: str
    subject: str
    date: str | None
    snippet: str
    label_ids: list[str] = field(default_factory=list)


@dataclass
class ThreadSummary:
    id: str
    history_id: str | None
    messages: list[MessageSummary]


def _headers_dict(headers: list[dict[str, str]]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in headers}


def _message_from_metadata(msg: dict[str, Any]) -> MessageSummary:
    payload = msg.get("payload", {}) or {}
    hdrs = _headers_dict(payload.get("headers") or [])
    return MessageSummary(
        id=msg["id"],
        thread_id=msg["threadId"],
        sender=hdrs.get("from", ""),
        subject=hdrs.get("subject", ""),
        date=hdrs.get("date"),
        snippet=msg.get("snippet", ""),
        label_ids=list(msg.get("labelIds") or []),
    )


class GmailReadClient:
    """Thin, read-only wrapper around the Gmail API.

    Callers should generally use :func:`get_client` which handles the stored
    credentials automatically.
    """

    def __init__(self, service: Resource) -> None:
        self._service = service

    # -------- Discovery --------

    def get_profile(self) -> dict[str, Any]:
        return self._service.users().getProfile(userId="me").execute()

    def list_labels(self) -> list[dict[str, Any]]:
        result = self._service.users().labels().list(userId="me").execute()
        return list(result.get("labels") or [])

    # -------- Message listing --------

    def list_recent_message_summaries(
        self, max_results: int = 10, query: str | None = None
    ) -> list[MessageSummary]:
        """Return header-only summaries of the most recent messages.

        Uses the ``metadata`` format so message bodies are never fetched.
        """
        list_kwargs: dict[str, Any] = {"userId": "me", "maxResults": max_results}
        if query:
            list_kwargs["q"] = query
        listing = self._service.users().messages().list(**list_kwargs).execute()

        summaries: list[MessageSummary] = []
        for stub in listing.get("messages", []) or []:
            msg = (
                self._service.users()
                .messages()
                .get(
                    userId="me",
                    id=stub["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            summaries.append(_message_from_metadata(msg))
        return summaries

    # -------- Full message access --------

    def get_message(self, message_id: str, message_format: str = "full") -> dict[str, Any]:
        """Fetch one message as a raw Gmail resource.

        Still read-only — ``users().messages().get`` cannot modify anything.
        The ``full`` format is what the classification engine needs, because
        headers alone can't tell a receipt from an advert.
        """
        return (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format=message_format)
            .execute()
        )

    def list_recent_messages(
        self, max_results: int = 10, query: str | None = None
    ) -> list[dict[str, Any]]:
        """Return full message resources for the most recent messages."""
        list_kwargs: dict[str, Any] = {"userId": "me", "maxResults": max_results}
        if query:
            list_kwargs["q"] = query
        listing = self._service.users().messages().list(**list_kwargs).execute()

        return [
            self.get_message(stub["id"])
            for stub in listing.get("messages", []) or []
        ]

    def list_message_ids(
        self,
        query: str | None = None,
        max_results: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """One page of id-only message stubs matching ``query`` — the raw
        ``messages.list`` response (``messages`` as ``{id, threadId}``
        stubs, ``nextPageToken``, ``resultSizeEstimate``), not full message
        content. Still read-only.

        Unlike :meth:`list_recent_messages`, this never fetches a message
        body itself — it exists so a caller that needs to page through a
        large, bounded date range (Phase 15's 12-month historical sweep)
        can walk the id list a page at a time and fetch full messages only
        as it actually processes each page, rather than pulling everything
        into memory in one call.
        """
        kwargs: dict[str, Any] = {"userId": "me", "maxResults": max_results}
        if query:
            kwargs["q"] = query
        if page_token:
            kwargs["pageToken"] = page_token
        return self._service.users().messages().list(**kwargs).execute()

    # -------- Thread access --------

    def get_thread(self, thread_id: str) -> ThreadSummary:
        raw = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata",
                 metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        msgs = [_message_from_metadata(m) for m in raw.get("messages") or []]
        return ThreadSummary(
            id=raw["id"],
            history_id=raw.get("historyId"),
            messages=msgs,
        )

    def get_thread_full(self, thread_id: str) -> dict[str, Any]:
        """The whole thread, every message's full content, in one API call.

        Still read-only — ``threads().get`` cannot modify anything. Used by
        the Phase 13 real-time poller (and available to any future caller)
        that needs accurate thread context — ``thread_message_count``,
        whether the user has participated — which a single-message fetch
        can't supply on its own. Pass the raw result to
        :func:`app.classification.message.from_gmail_thread`.
        """
        return (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )

    # -------- History (change detection) --------

    def list_history(
        self,
        start_history_id: str,
        history_types: list[str] | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """One page of ``users.history.list`` — the change feed Phase 13's
        poller uses instead of re-listing the whole mailbox on a timer.

        Still read-only. Gmail expires old history ids (mailbox history is
        only retained for a limited window); callers must be ready to handle
        an ``HttpError`` with status 404 from this call by re-baselining
        (see :mod:`app.scheduling.history`).
        """
        kwargs: dict[str, Any] = {"userId": "me", "startHistoryId": start_history_id}
        if history_types:
            kwargs["historyTypes"] = history_types
        if page_token:
            kwargs["pageToken"] = page_token
        return self._service.users().history().list(**kwargs).execute()


def get_client() -> GmailReadClient:
    """Load the stored token and return a ready-to-use read client.

    Raises ``NotConnectedError`` (a ``FileNotFoundError``) if no token is stored.
    """
    stored = load_stored_token_or_raise()
    return GmailReadClient(service=build_service(*_GMAIL_API, stored=stored))


def format_summaries_for_display(summaries: Iterable[MessageSummary]) -> list[dict[str, str]]:
    """Return a JSON-safe view for the /gmail/preview route."""
    return [
        {
            "id": s.id,
            "thread_id": s.thread_id,
            "from": s.sender,
            "subject": s.subject,
            "date": s.date or "",
            "snippet": s.snippet,
            "labels": ",".join(s.label_ids),
        }
        for s in summaries
    ]


__all__ = (
    "GmailReadClient",
    "MessageSummary",
    "ThreadSummary",
    "get_client",
    "format_summaries_for_display",
)
