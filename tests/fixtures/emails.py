"""Helpers for building email fixtures in classification tests."""

from __future__ import annotations

import base64
from typing import Any

from app.classification.message import Attachment, EmailMessage

DEFAULT_USER = "jikahu@gmail.com"


def make_message(
    sender: str = "someone@example.com",
    subject: str = "",
    body: str = "",
    snippet: str = "",
    sender_name: str = "",
    to: list[str] | None = None,
    headers: dict[str, str] | None = None,
    labels: list[str] | None = None,
    attachments: list[Attachment] | None = None,
    message_id: str = "msg-1",
    thread_id: str = "thread-1",
    thread_message_count: int = 1,
    user_in_thread: bool = False,
    sent_by_user: bool = False,
    reply_to: str = "",
) -> EmailMessage:
    """Build an :class:`EmailMessage` with sensible defaults.

    Defaults matter here: a real inbox message has a ``To`` header, so the
    fixture supplies one. Omitting it would make every fixture look like a
    blind mass mailing and quietly change what the rules see.
    """
    return EmailMessage(
        message_id=message_id,
        thread_id=thread_id,
        sender_name=sender_name,
        sender_email=sender.lower(),
        to=to if to is not None else [DEFAULT_USER],
        reply_to=reply_to,
        subject=subject,
        snippet=snippet,
        body_text=body,
        label_ids=labels if labels is not None else ["INBOX"],
        headers={k.lower(): v for k, v in (headers or {}).items()},
        attachments=attachments or [],
        thread_message_count=thread_message_count,
        user_in_thread=user_in_thread,
        sent_by_user=sent_by_user,
    )


def bulk_headers(list_id: str = "campaign.example.com") -> dict[str, str]:
    """The header set a typical mass mailing carries."""
    return {
        "list-unsubscribe": "<mailto:unsubscribe@example.com>",
        "list-id": f"<{list_id}>",
        "precedence": "bulk",
    }


def substack_headers(publication: str = "goodwriter") -> dict[str, str]:
    return {
        "list-unsubscribe": f"<https://{publication}.substack.com/action/unsubscribe>",
        "list-id": f"<{publication}.substack.com>",
    }


def pdf(filename: str = "document.pdf") -> Attachment:
    return Attachment(filename=filename, mime_type="application/pdf", size_bytes=1024)


# --------------------------------------------------------------------
# Raw Gmail API resources, for parsing tests
# --------------------------------------------------------------------


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def gmail_message(
    message_id: str = "gm-1",
    thread_id: str = "gt-1",
    headers: dict[str, str] | None = None,
    plain_body: str | None = None,
    html_body: str | None = None,
    attachments: list[tuple[str, str]] | None = None,
    labels: list[str] | None = None,
    snippet: str = "",
) -> dict[str, Any]:
    """Build a Gmail API message resource shaped like the real thing."""
    header_list = [
        {"name": name, "value": value} for name, value in (headers or {}).items()
    ]

    parts: list[dict[str, Any]] = []
    if plain_body is not None:
        parts.append(
            {"mimeType": "text/plain", "body": {"data": _b64(plain_body)}, "filename": ""}
        )
    if html_body is not None:
        parts.append(
            {"mimeType": "text/html", "body": {"data": _b64(html_body)}, "filename": ""}
        )
    for filename, mime in attachments or []:
        parts.append(
            {
                "mimeType": mime,
                "filename": filename,
                "body": {"attachmentId": "att-1", "size": 2048},
            }
        )

    payload: dict[str, Any] = {"headers": header_list, "mimeType": "multipart/mixed"}
    if parts:
        payload["parts"] = parts
    else:
        payload["body"] = {}

    return {
        "id": message_id,
        "threadId": thread_id,
        "snippet": snippet,
        "labelIds": labels if labels is not None else ["INBOX"],
        "payload": payload,
    }


__all__ = (
    "DEFAULT_USER",
    "bulk_headers",
    "gmail_message",
    "make_message",
    "pdf",
    "substack_headers",
)
