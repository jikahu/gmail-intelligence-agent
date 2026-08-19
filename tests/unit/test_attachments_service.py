"""Fetching attachments, and how they affect classification.

The invariant that matters most here, from CLAUDE.md §11:

> an attachment-processing failure must never by itself route an email to Review
"""

from __future__ import annotations

import base64

import pytest

from app.attachments.models import AttachmentReport, ExtractedAttachment, ExtractionStatus
from app.attachments.service import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    decode_attachment_data,
    process_message,
)
from app.classification.context import ClassificationContext
from app.classification.engine import classify
from app.classification.labels import Label
from app.classification.message import Attachment, from_gmail
from tests.fixtures.attachments import (
    docx_bytes,
    gmail_attachment_payload,
    pdf_with_text,
    windows_executable,
    zip_bomb,
)
from tests.fixtures.emails import DEFAULT_USER, bulk_headers, gmail_message, make_message

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class FakeGmailService:
    """Just enough Gmail to serve attachment bytes."""

    def __init__(self, payloads: dict[str, bytes], fail: bool = False) -> None:
        self.payloads = payloads
        self.fail = fail
        self.requested: list[str] = []

    # Mirrors the googleapiclient call chain the service uses.
    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def get(self, userId: str, messageId: str, id: str):
        self.requested.append(id)
        service = self

        class _Execute:
            def execute(self_inner):
                if service.fail:
                    raise RuntimeError("network down")
                data = service.payloads.get(id)
                if data is None:
                    raise KeyError(id)
                return gmail_attachment_payload(data)

        return _Execute()


class FakeGmailClient:
    def __init__(self, payloads: dict[str, bytes], fail: bool = False) -> None:
        self._service = FakeGmailService(payloads, fail)


def message_with(attachments: list[Attachment]):
    message = make_message(sender="sender@example.com", subject="Documents")
    message.attachments = attachments
    return message


# --------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------


def test_base64url_payload_is_decoded() -> None:
    raw = b"hello attachment"
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    assert decode_attachment_data(encoded) == raw


def test_malformed_payload_decodes_to_none_rather_than_raising() -> None:
    assert decode_attachment_data("!!!not base64!!!") is None
    assert decode_attachment_data("") is None


def test_attachments_are_downloaded_and_read() -> None:
    data = docx_bytes(["Policy number ABC123", "Premium due 1 September"])
    client = FakeGmailClient({"att-1": data})
    message = message_with(
        [Attachment(filename="policy.docx", mime_type=DOCX_MIME, attachment_id="att-1")]
    )

    report = process_message(message, client)

    assert client._service.requested == ["att-1"]
    assert report.succeeded_count == 1
    assert "Policy number ABC123" in report.combined_text
    assert message.attachments[0].extracted_text
    assert message.attachments[0].extraction_status == "extracted"


def test_inline_attachments_need_no_download() -> None:
    encoded = base64.urlsafe_b64encode(b"Invoice total 99.00").decode().rstrip("=")
    client = FakeGmailClient({})
    message = message_with(
        [Attachment(filename="note.txt", mime_type="text/plain", inline_data=encoded)]
    )

    report = process_message(message, client)

    assert client._service.requested == []
    assert "Invoice total" in report.combined_text


def test_a_download_failure_is_recorded_not_raised() -> None:
    client = FakeGmailClient({}, fail=True)
    message = message_with(
        [Attachment(filename="thing.pdf", mime_type="application/pdf", attachment_id="x")]
    )

    report = process_message(message, client)

    assert report.items[0].status is ExtractionStatus.NOT_ATTEMPTED
    assert not report.combined_text


def test_oversized_attachments_are_never_downloaded() -> None:
    client = FakeGmailClient({"big": b"x" * 10})
    message = message_with(
        [
            Attachment(
                filename="huge.pdf",
                mime_type="application/pdf",
                size_bytes=999_999_999,
                attachment_id="big",
            )
        ]
    )

    report = process_message(message, client)

    assert client._service.requested == []      # no bandwidth spent
    assert report.items[0].status is ExtractionStatus.TOO_LARGE


def test_too_many_attachments_are_capped() -> None:
    attachments = [
        Attachment(filename=f"f{i}.txt", mime_type="text/plain", inline_data="aGk")
        for i in range(MAX_ATTACHMENTS_PER_MESSAGE + 5)
    ]
    report = process_message(message_with(attachments), FakeGmailClient({}))

    assert len(report.items) == MAX_ATTACHMENTS_PER_MESSAGE + 1  # + the summary row
    assert "more attachments" in report.items[-1].filename


def test_a_message_without_attachments_produces_an_empty_report() -> None:
    report = process_message(make_message(subject="no files"), FakeGmailClient({}))

    assert report.items == []
    assert report.combined_text == ""


def test_attachment_ids_are_parsed_from_gmail() -> None:
    raw = gmail_message(
        headers={"From": "a@b.com", "To": DEFAULT_USER, "Subject": "Docs"},
        plain_body="see attached",
        attachments=[("statement.pdf", "application/pdf")],
    )
    message = from_gmail(raw)

    assert message.attachments[0].attachment_id == "att-1"
    assert message.attachments[0].filename == "statement.pdf"


# --------------------------------------------------------------------
# The failure-is-inert invariant
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "mime"),
    [
        ("corrupt", b"%PDF-1.4 garbage", "application/pdf"),
        ("encrypted-ish", b"PK\x03\x04 broken", DOCX_MIME),
        ("bomb", zip_bomb(), DOCX_MIME),
        ("executable", windows_executable(), "application/x-msdownload"),
        ("image", b"\xff\xd8\xff\xe0 jpeg", "image/jpeg"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_an_unreadable_attachment_never_sends_an_email_to_review(
    name, data, mime
) -> None:
    """The §11 guarantee, tested across every failure mode."""
    encoded = base64.urlsafe_b64encode(data).decode().rstrip("=")
    message = make_message(
        sender="promo@bulk.example",
        subject="Limited time offer inside",
        headers=bulk_headers(),
    )
    message.attachments = [
        Attachment(filename=f"file-{name}", mime_type=mime, inline_data=encoded)
    ]

    process_message(message, FakeGmailClient({}))
    decision = classify(message, ClassificationContext())

    assert not decision.review, f"{name} caused a Review"
    assert decision.protected


def test_classification_is_identical_with_and_without_a_failed_attachment() -> None:
    """A broken file adds nothing — and takes nothing away."""
    context = ClassificationContext()

    without = make_message(sender="a@b.com", subject="Quarterly figures")
    baseline = classify(without, context)

    with_broken = make_message(sender="a@b.com", subject="Quarterly figures")
    with_broken.attachments = [
        Attachment(
            filename="broken.pdf",
            mime_type="application/pdf",
            inline_data=base64.urlsafe_b64encode(b"%PDF nope").decode().rstrip("="),
        )
    ]
    process_message(with_broken, FakeGmailClient({}))
    after = classify(with_broken, context)

    # The attachment protects the message (§8) but changes nothing else.
    assert after.priority is baseline.priority
    assert not after.review
    assert after.protected


# --------------------------------------------------------------------
# Attachment contents inform classification
# --------------------------------------------------------------------


def test_a_tax_document_is_recognised_from_its_contents() -> None:
    data = docx_bytes(
        ["Form W-2 Wage and Tax Statement", "Employer Identification Number 12-3456789"]
    )
    encoded = base64.urlsafe_b64encode(data).decode().rstrip("=")
    message = make_message(sender="payroll@corp.com", subject="Your documents")
    message.attachments = [
        Attachment(filename="document.docx", mime_type=DOCX_MIME, inline_data=encoded)
    ]

    process_message(message, FakeGmailClient({}))
    decision = classify(message, ClassificationContext())

    assert decision.has(Label.IMPORTANT_DOCUMENT)
    assert any("attachment contents" in note for note in decision.rules_triggered)


def test_a_promotional_pdf_is_still_not_an_important_document() -> None:
    """Reading the file must not turn every advert into a record."""
    encoded = (
        base64.urlsafe_b64encode(pdf_with_text("Half price sale this weekend"))
        .decode()
        .rstrip("=")
    )
    message = make_message(
        sender="promo@shop.example",
        subject="Limited time offer inside",
        headers=bulk_headers(),
    )
    message.attachments = [
        Attachment(filename="offer.pdf", mime_type="application/pdf", inline_data=encoded)
    ]

    process_message(message, FakeGmailClient({}))
    decision = classify(message, ClassificationContext())

    assert not decision.has(Label.IMPORTANT_DOCUMENT)
    assert decision.protected      # attachments still protect
    assert not decision.review


def test_message_attachment_text_is_lowercased_and_joined() -> None:
    message = make_message()
    message.attachments = [
        Attachment(filename="a.txt", mime_type="text/plain", extracted_text="First Part"),
        Attachment(filename="b.txt", mime_type="text/plain", extracted_text="Second Part"),
    ]

    assert message.attachment_text == "first part second part"


def test_attachment_text_is_empty_before_extraction_runs() -> None:
    message = make_message()
    message.attachments = [Attachment(filename="a.pdf", mime_type="application/pdf")]

    assert message.attachment_text == ""


# --------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------


def test_report_view_never_includes_the_extracted_text() -> None:
    message = message_with(
        [
            Attachment(
                filename="secret.txt",
                mime_type="text/plain",
                inline_data=base64.urlsafe_b64encode(b"ACCOUNT 12345678")
                .decode()
                .rstrip("="),
            )
        ]
    )
    report = process_message(message, FakeGmailClient({}))

    assert "ACCOUNT 12345678" not in str(report.as_dict())
    assert report.as_dict()["items"][0]["has_text"] is True
    assert report.as_dict()["items"][0]["characters_extracted"] > 0


def test_report_flags_dangerous_attachments() -> None:
    encoded = base64.urlsafe_b64encode(windows_executable()).decode().rstrip("=")
    message = message_with(
        [Attachment(filename="setup.exe", mime_type="application/x-msdownload", inline_data=encoded)]
    )

    report = process_message(message, FakeGmailClient({}))

    assert report.any_dangerous
    assert report.as_dict()["any_dangerous"] is True


def test_every_status_has_a_plain_english_explanation() -> None:
    for status in ExtractionStatus:
        item = ExtractedAttachment(filename="x", status=status)
        assert item.explanation
        assert not item.explanation.startswith("Unknown")


def test_empty_report_is_well_formed() -> None:
    report = AttachmentReport()

    assert report.as_dict()["count"] == 0
    assert not report.any_failed
    assert not report.any_dangerous
