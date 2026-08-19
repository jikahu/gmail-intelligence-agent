"""Fetching and reading an email's attachments.

Downloading is read-only — ``users().messages().attachments().get`` cannot
modify anything, and the read client this goes through has no mutation methods
at all (asserted by a test since Phase 1).

The important behaviour is what happens when things go wrong. Every failure
path here ends the same way: the attachment is recorded as unreadable and the
email carries on being classified exactly as it would have been. CLAUDE.md §11
requires that an attachment-processing failure never by itself routes an email
to Review, and the shape of this module is what makes that true — nothing it
returns is capable of *removing* information, only adding it.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from app.attachments.extract import extract
from app.attachments.models import AttachmentReport, ExtractedAttachment, ExtractionStatus
from app.attachments.types import MAX_ATTACHMENT_BYTES, safe_display_name
from app.classification.message import Attachment, EmailMessage
from app.logging_config import get_logger

log = get_logger("app.attachments.service")

#: Most attachments we'll open for a single email. A message with 40 files is
#: not something we need to read exhaustively to classify.
MAX_ATTACHMENTS_PER_MESSAGE = 10


def decode_attachment_data(data: str) -> bytes | None:
    """Decode Gmail's URL-safe base64 attachment payload."""
    if not data:
        return None
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding)
    except (binascii.Error, ValueError):
        return None


def download_attachment(
    gmail_client: Any, message_id: str, attachment: Attachment
) -> bytes | None:
    """Fetch one attachment's bytes. Returns ``None`` on any failure."""
    if attachment.inline_data:
        return decode_attachment_data(attachment.inline_data)

    if not attachment.attachment_id:
        return None

    try:
        payload = (
            gmail_client._service.users()  # noqa: SLF001 — read-only accessor
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment.attachment_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — a download failure is not fatal
        log.warning(
            "attachment_download_failed",
            extra={
                "attachment_name": safe_display_name(attachment.filename),
                "error": str(exc)[:200],
            },
        )
        return None

    return decode_attachment_data(str(payload.get("data") or ""))


def process_message(
    message: EmailMessage,
    gmail_client: Any | None = None,
    download: bool = True,
) -> AttachmentReport:
    """Read every attachment on one message and record the outcome.

    Results are written back onto the message's :class:`Attachment` objects so
    the classification engine can use the text, and returned as a report for
    the dashboard.
    """
    report = AttachmentReport()
    if not message.attachments:
        return report

    for attachment in message.attachments[:MAX_ATTACHMENTS_PER_MESSAGE]:
        data: bytes | None = None

        if attachment.size_bytes and attachment.size_bytes > MAX_ATTACHMENT_BYTES:
            # Don't spend bandwidth on something we've already decided not to
            # open. `extract` records the TOO_LARGE status from the declared
            # size alone.
            pass
        elif download and gmail_client is not None:
            data = download_attachment(gmail_client, message.message_id, attachment)
        elif attachment.inline_data:
            data = decode_attachment_data(attachment.inline_data)

        result = extract(
            filename=attachment.filename,
            data=data,
            mime_type=attachment.mime_type,
            declared_size=attachment.size_bytes,
        )

        attachment.extracted_text = result.text
        attachment.extraction_status = result.status.value
        report.items.append(result)

    skipped = len(message.attachments) - len(report.items)
    if skipped > 0:
        report.items.append(
            ExtractedAttachment(
                filename=f"({skipped} more attachments)",
                status=ExtractionStatus.UNSUPPORTED,
                error=f"only the first {MAX_ATTACHMENTS_PER_MESSAGE} were read",
            )
        )

    log.info(
        "attachments_processed",
        extra={
            "message_id": message.message_id,
            "attachment_count": len(report.items),
            "read_successfully": report.succeeded_count,
            "any_failed": report.any_failed,
            "any_dangerous": report.any_dangerous,
        },
    )
    return report


__all__ = (
    "MAX_ATTACHMENTS_PER_MESSAGE",
    "decode_attachment_data",
    "download_attachment",
    "process_message",
)
