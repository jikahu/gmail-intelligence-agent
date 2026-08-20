"""A normalized, provider-agnostic view of one email.

Everything downstream of this module works on :class:`EmailMessage`, never on
raw Gmail JSON. That keeps the rules engine testable with hand-written
fixtures and means a future provider change touches only :func:`from_gmail`.

Safety note: the body text captured here is **data**, never instructions.
Nothing in this module interprets, follows, or executes anything found in a
message (CLAUDE.md §16).
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Iterable

#: Gmail's own category labels, which are useful classification signals.
GMAIL_CATEGORY_PROMOTIONS = "CATEGORY_PROMOTIONS"
GMAIL_CATEGORY_SOCIAL = "CATEGORY_SOCIAL"
GMAIL_CATEGORY_UPDATES = "CATEGORY_UPDATES"
GMAIL_CATEGORY_FORUMS = "CATEGORY_FORUMS"

#: How much body text we retain. Enough for keyword rules, small enough that we
#: are never holding (or later sending to an AI provider) an entire newsletter.
BODY_TEXT_LIMIT = 20_000

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_STYLE_SCRIPT_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)


def _decode_base64url(data: str) -> str:
    """Decode Gmail's URL-safe base64 body data. Never raises."""
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(data + padding)
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def strip_html(html: str) -> str:
    """Reduce an HTML body to plain text.

    This is for *reading* the message as data. It removes script and style
    blocks outright rather than trying to sanitize them, because nothing here
    ever renders the result as markup.
    """
    without_code = _STYLE_SCRIPT_RE.sub(" ", html)
    text = _HTML_TAG_RE.sub(" ", without_code)
    for entity, replacement in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, replacement)
    return _WHITESPACE_RE.sub(" ", text).strip()


def split_address(raw: str) -> tuple[str, str]:
    """Return ``(display_name, email)`` from a ``From``-style header value."""
    name, address = parseaddr(raw or "")
    return name.strip(), address.strip().lower()


def domain_of(email_address: str) -> str:
    """Return the lowercased domain of an address, or ``""``."""
    _, _, domain = (email_address or "").partition("@")
    return domain.strip().lower()


def registrable_domain(domain: str) -> str:
    """Return the last two labels of a domain (``mail.chase.com`` → ``chase.com``).

    A deliberate simplification: it is not a public-suffix list, so it treats
    ``example.co.uk`` as ``co.uk``. Callers use it only as a *widening* hint
    alongside the exact domain, never as the sole basis for trusting a sender.
    """
    parts = [p for p in (domain or "").split(".") if p]
    if len(parts) < 2:
        return domain or ""
    return ".".join(parts[-2:])


def _addresses(raw: str) -> list[str]:
    """Split a To/Cc header into lowercased addresses."""
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.split(","):
        _, address = split_address(chunk)
        if address:
            out.append(address)
    return out


@dataclass
class Attachment:
    """One attachment's metadata, as Gmail reports it.

    Attachment *contents* are never read or sent anywhere — only what Gmail's
    own message payload already exposes (filename, mime type, size), which is
    enough for the rules engine's document-shaped-attachment checks.
    """

    filename: str
    mime_type: str
    size_bytes: int = 0
    #: Gmail's handle for downloading the bytes. Empty for inline parts.
    attachment_id: str = ""
    #: Base64url payload, present when Gmail inlined a small attachment.
    inline_data: str = ""


@dataclass
class EmailMessage:
    """One email, normalized."""

    message_id: str
    thread_id: str = ""
    sender_name: str = ""
    sender_email: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    reply_to: str = ""
    subject: str = ""
    snippet: str = ""
    body_text: str = ""
    date: datetime | None = None
    label_ids: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)
    #: Number of messages in the containing thread, when known.
    thread_message_count: int = 1
    #: True when the user themselves sent this message.
    sent_by_user: bool = False
    #: True when the user has sent at least one message in this thread.
    user_in_thread: bool = False

    # -------- Derived views --------

    @property
    def sender_domain(self) -> str:
        return domain_of(self.sender_email)

    @property
    def sender_registrable_domain(self) -> str:
        return registrable_domain(self.sender_domain)

    @property
    def has_attachments(self) -> bool:
        return bool(self.attachments)

    @property
    def in_inbox(self) -> bool:
        return "INBOX" in self.label_ids

    @property
    def is_unread(self) -> bool:
        return "UNREAD" in self.label_ids

    @property
    def is_starred(self) -> bool:
        return "STARRED" in self.label_ids

    @property
    def is_active_thread(self) -> bool:
        """A back-and-forth the user is part of."""
        return self.thread_message_count > 1 and self.user_in_thread

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def has_header(self, name: str) -> bool:
        return bool(self.headers.get(name.lower(), "").strip())

    @property
    def searchable_text(self) -> str:
        """Subject + snippet + body, lowercased — what keyword rules read."""
        return " ".join(
            part for part in (self.subject, self.snippet, self.body_text) if part
        ).lower()

    @property
    def subject_and_snippet(self) -> str:
        """Just the headline text, for rules that must not read the whole body."""
        return f"{self.subject} {self.snippet}".lower()

    def safe_reference(self, limit: int = 80) -> str:
        """A short, log-safe description. Never the body (CLAUDE.md §16)."""
        subject = (self.subject or "(no subject)")[:limit]
        return f"{self.sender_email or '(unknown sender)'} — {subject}"


# --------------------------------------------------------------------
# Gmail parsing
# --------------------------------------------------------------------


def _walk_parts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield every MIME part in the payload tree, depth first."""
    yield payload
    for part in payload.get("parts") or []:
        yield from _walk_parts(part)


def _extract_body(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    plain: list[str] = []
    html: list[str] = []

    for part in _walk_parts(payload):
        mime = (part.get("mimeType") or "").lower()
        data = (part.get("body") or {}).get("data") or ""
        if not data:
            continue
        if mime == "text/plain":
            plain.append(_decode_base64url(data))
        elif mime == "text/html":
            html.append(_decode_base64url(data))

    if plain:
        text = "\n".join(plain)
    elif html:
        text = strip_html("\n".join(html))
    else:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()[:BODY_TEXT_LIMIT]


def _extract_attachments(payload: dict[str, Any]) -> list[Attachment]:
    found: list[Attachment] = []
    for part in _walk_parts(payload):
        filename = (part.get("filename") or "").strip()
        if not filename:
            continue
        body = part.get("body") or {}
        found.append(
            Attachment(
                filename=filename,
                mime_type=(part.get("mimeType") or "").lower(),
                size_bytes=int(body.get("size") or 0),
                attachment_id=str(body.get("attachmentId") or ""),
                inline_data=str(body.get("data") or ""),
            )
        )
    return found


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def from_gmail(
    message: dict[str, Any],
    user_email: str = "",
    thread_message_count: int = 1,
    user_in_thread: bool = False,
) -> EmailMessage:
    """Build an :class:`EmailMessage` from a Gmail API message resource.

    Works with either the ``metadata`` or ``full`` format — body text is simply
    empty when Gmail wasn't asked for it.
    """
    payload = message.get("payload") or {}
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in (payload.get("headers") or [])
    }

    sender_name, sender_email = split_address(headers.get("from", ""))
    normalized_user = (user_email or "").strip().lower()

    label_ids = list(message.get("labelIds") or [])
    sent_by_user = bool(
        (normalized_user and sender_email == normalized_user) or "SENT" in label_ids
    )

    return EmailMessage(
        message_id=str(message.get("id", "")),
        thread_id=str(message.get("threadId", "")),
        sender_name=sender_name,
        sender_email=sender_email,
        to=_addresses(headers.get("to", "")),
        cc=_addresses(headers.get("cc", "")),
        reply_to=split_address(headers.get("reply-to", ""))[1],
        subject=headers.get("subject", ""),
        snippet=str(message.get("snippet", "")),
        body_text=_extract_body(payload),
        date=_parse_date(headers.get("date", "")),
        label_ids=label_ids,
        headers=headers,
        attachments=_extract_attachments(payload),
        thread_message_count=thread_message_count,
        user_in_thread=user_in_thread or sent_by_user,
        sent_by_user=sent_by_user,
    )


def from_gmail_thread(
    thread: dict[str, Any], user_email: str = ""
) -> list[EmailMessage]:
    """Parse a whole Gmail thread, with thread context filled in on each message.

    Thread context matters because CLAUDE.md §8 protects conversations the user
    is actively part of.
    """
    raw_messages = thread.get("messages") or []
    normalized_user = (user_email or "").strip().lower()

    participated = any(
        split_address(
            {
                str(h.get("name", "")).lower(): str(h.get("value", ""))
                for h in ((m.get("payload") or {}).get("headers") or [])
            }.get("from", "")
        )[1]
        == normalized_user
        or "SENT" in (m.get("labelIds") or [])
        for m in raw_messages
    )

    return [
        from_gmail(
            raw,
            user_email=user_email,
            thread_message_count=len(raw_messages),
            user_in_thread=participated,
        )
        for raw in raw_messages
    ]


__all__ = (
    "Attachment",
    "BODY_TEXT_LIMIT",
    "EmailMessage",
    "GMAIL_CATEGORY_FORUMS",
    "GMAIL_CATEGORY_PROMOTIONS",
    "GMAIL_CATEGORY_SOCIAL",
    "GMAIL_CATEGORY_UPDATES",
    "domain_of",
    "from_gmail",
    "from_gmail_thread",
    "registrable_domain",
    "split_address",
    "strip_html",
)
