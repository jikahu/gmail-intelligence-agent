"""The result of trying to read one attachment.

Every attempt produces one of these, including the failures. That's the point:
CLAUDE.md §11 requires that a processing failure is *recorded* rather than
silently swallowed, and — crucially — that **a failure never by itself routes
an email to Review**.

That guarantee is structural rather than a rule someone has to remember.
Emails with attachments are hard-protected (§8), so an attachment that can't be
read simply contributes no extra information. Nothing downstream reads
:attr:`ExtractedAttachment.status` and decides to hide anything; the only thing
extracted text can do is *add* a label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.attachments.types import AttachmentKind


class ExtractionStatus(str, Enum):
    """How reading an attachment turned out."""

    #: Text was read successfully.
    EXTRACTED = "extracted"
    #: Opened fine, but there was no text in it (a scanned PDF, an empty file).
    EMPTY = "empty"
    #: A type we recognise but don't read in V1 — images, archives.
    UNSUPPORTED = "unsupported"
    #: Bigger than we're willing to open.
    TOO_LARGE = "too_large"
    #: Password-protected.
    ENCRYPTED = "encrypted"
    #: Malformed or truncated.
    CORRUPTED = "corrupted"
    #: An executable or macro-enabled file. Refused without being opened.
    BLOCKED = "blocked"
    #: The extraction library isn't installed.
    LIBRARY_MISSING = "library_missing"
    #: Anything else went wrong.
    FAILED = "failed"
    #: We haven't tried yet.
    NOT_ATTEMPTED = "not_attempted"

    @property
    def is_success(self) -> bool:
        return self in {ExtractionStatus.EXTRACTED, ExtractionStatus.EMPTY}

    @property
    def is_failure(self) -> bool:
        """True for statuses meaning "we tried and couldn't"."""
        return self in {
            ExtractionStatus.TOO_LARGE,
            ExtractionStatus.ENCRYPTED,
            ExtractionStatus.CORRUPTED,
            ExtractionStatus.LIBRARY_MISSING,
            ExtractionStatus.FAILED,
        }


#: Plain-English explanations, shown on the dashboard.
STATUS_EXPLANATIONS: dict[ExtractionStatus, str] = {
    ExtractionStatus.EXTRACTED: "Read successfully.",
    ExtractionStatus.EMPTY: "Opened, but there was no readable text in it.",
    ExtractionStatus.UNSUPPORTED: "This kind of file isn't read for text.",
    ExtractionStatus.TOO_LARGE: "Too large to open safely.",
    ExtractionStatus.ENCRYPTED: "Password-protected, so it couldn't be opened.",
    ExtractionStatus.CORRUPTED: "The file appears to be damaged or incomplete.",
    ExtractionStatus.BLOCKED: (
        "This is a program file. It was not opened, and it was not run."
    ),
    ExtractionStatus.LIBRARY_MISSING: (
        "The software needed to read this type isn't installed."
    ),
    ExtractionStatus.FAILED: "Something went wrong while reading it.",
    ExtractionStatus.NOT_ATTEMPTED: "Not looked at yet.",
}


@dataclass
class ExtractedAttachment:
    """One attachment, and what we managed to learn from it."""

    filename: str
    mime_type: str = ""
    size_bytes: int = 0
    kind: AttachmentKind = AttachmentKind.UNKNOWN
    status: ExtractionStatus = ExtractionStatus.NOT_ATTEMPTED
    text: str = ""
    #: Short technical detail; the user-facing wording is :attr:`explanation`.
    error: str = ""
    #: Things worth noticing that aren't errors — an embedded script in a PDF,
    #: a filename whose type doesn't match its contents.
    warnings: tuple[str, ...] = ()
    #: Structure counts, where meaningful.
    page_count: int = 0
    row_count: int = 0
    truncated: bool = False

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def is_dangerous(self) -> bool:
        return (
            self.kind is AttachmentKind.EXECUTABLE
            or self.status is ExtractionStatus.BLOCKED
        )

    @property
    def explanation(self) -> str:
        """One plain sentence about what happened."""
        base = STATUS_EXPLANATIONS.get(self.status, "Unknown outcome.")
        if self.warnings:
            return f"{base} Note: {self.warnings[0]}"
        return base

    def as_dict(self) -> dict[str, object]:
        """JSON-safe view. Never includes the extracted text itself."""
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "kind": self.kind.value,
            "status": self.status.value,
            "explanation": self.explanation,
            "has_text": self.has_text,
            "characters_extracted": len(self.text),
            "page_count": self.page_count or None,
            "row_count": self.row_count or None,
            "truncated": self.truncated,
            "warnings": list(self.warnings),
            "dangerous": self.is_dangerous,
        }


@dataclass
class AttachmentReport:
    """Everything learned from one email's attachments."""

    items: list[ExtractedAttachment] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        """All extracted text, for keyword rules to read."""
        return "\n".join(item.text for item in self.items if item.has_text)

    @property
    def any_dangerous(self) -> bool:
        return any(item.is_dangerous for item in self.items)

    @property
    def any_failed(self) -> bool:
        return any(item.status.is_failure for item in self.items)

    @property
    def succeeded_count(self) -> int:
        return sum(1 for item in self.items if item.status.is_success)

    def as_dict(self) -> dict[str, object]:
        return {
            "count": len(self.items),
            "read_successfully": self.succeeded_count,
            "any_failed": self.any_failed,
            "any_dangerous": self.any_dangerous,
            "items": [item.as_dict() for item in self.items],
        }


__all__ = (
    "AttachmentReport",
    "ExtractedAttachment",
    "ExtractionStatus",
    "STATUS_EXPLANATIONS",
)
