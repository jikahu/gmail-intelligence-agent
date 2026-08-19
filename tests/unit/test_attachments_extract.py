"""Attachment extraction (CLAUDE.md §11).

The rule under test throughout: **attachments are read as information, never
executed** — and anything we can't read fails safely and inertly.
"""

from __future__ import annotations

import pytest

from app.attachments.extract import extract
from app.attachments.models import ExtractionStatus
from app.attachments.types import (
    DANGEROUS_EXTENSIONS,
    MAX_ATTACHMENT_BYTES,
    MAX_EXTRACTED_CHARS,
    AttachmentKind,
    classify_attachment,
    safe_display_name,
)
from tests.fixtures.attachments import (
    docx_bytes,
    docx_with_table,
    linux_executable,
    pdf_bytes,
    pdf_with_text,
    windows_executable,
    zip_bomb,
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# --------------------------------------------------------------------
# The types we do read
# --------------------------------------------------------------------


def test_plain_text_is_read() -> None:
    result = extract("notes.txt", b"Invoice total is 42.00", "text/plain")

    assert result.status is ExtractionStatus.EXTRACTED
    assert "Invoice total" in result.text
    assert result.kind is AttachmentKind.TEXT


def test_csv_is_read_as_rows() -> None:
    result = extract("data.csv", b"name,amount\nAlice,100\nBob,250", "text/csv")

    assert result.status is ExtractionStatus.EXTRACTED
    assert result.row_count == 3
    assert "Alice" in result.text
    assert "250" in result.text


def test_tab_separated_files_are_read() -> None:
    result = extract("data.tsv", b"name\tamount\nAlice\t100", "text/tab-separated-values")
    assert result.status is ExtractionStatus.EXTRACTED
    assert "Alice" in result.text


def test_docx_paragraphs_are_read() -> None:
    data = docx_bytes(["Employer Identification Number 12-3456789", "Wages: 84,000"])
    result = extract("w2.docx", data, DOCX_MIME)

    assert result.status is ExtractionStatus.EXTRACTED
    assert "Employer Identification Number" in result.text
    assert "84,000" in result.text


def test_docx_tables_are_read() -> None:
    data = docx_with_table([["Item", "Cost"], ["Premium", "1200"]])
    result = extract("policy.docx", data, DOCX_MIME)

    assert result.status is ExtractionStatus.EXTRACTED
    assert "Premium" in result.text
    assert "1200" in result.text


def test_pdf_text_is_read() -> None:
    result = extract("statement.pdf", pdf_with_text("Statement of account"), "application/pdf")

    assert result.status is ExtractionStatus.EXTRACTED
    assert "Statement of account" in result.text
    assert result.page_count == 1


def test_a_pdf_with_no_text_is_empty_not_an_error() -> None:
    """A scanned PDF is a normal thing, not a failure."""
    result = extract("scan.pdf", pdf_bytes(), "application/pdf")

    assert result.status is ExtractionStatus.EMPTY
    assert result.status.is_success
    assert not result.status.is_failure
    assert result.page_count == 1


def test_utf16_text_is_decoded() -> None:
    result = extract("notes.txt", "Grüße und Rechnung".encode("utf-16"), "text/plain")
    assert result.status is ExtractionStatus.EXTRACTED
    assert "Rechnung" in result.text


def test_an_empty_file_reports_empty_not_a_download_failure() -> None:
    result = extract("empty.txt", b"", "text/plain")

    assert result.status is ExtractionStatus.EMPTY
    assert result.status.is_success


def test_a_failed_download_is_distinguishable_from_an_empty_file() -> None:
    result = extract("thing.txt", None, "text/plain")

    assert result.status is ExtractionStatus.NOT_ATTEMPTED
    assert "could not be downloaded" in result.error


# --------------------------------------------------------------------
# Never executed
# --------------------------------------------------------------------


def test_executables_are_refused_without_being_opened() -> None:
    result = extract("invoice.exe", windows_executable(), "application/x-msdownload")

    assert result.status is ExtractionStatus.BLOCKED
    assert result.kind is AttachmentKind.EXECUTABLE
    assert result.is_dangerous
    assert result.text == ""
    assert "was not opened, and it was not run" in result.explanation
    # No duplication: the status sentence already says it, so there's no
    # warning repeating it.
    assert result.warnings == ()


def test_an_executable_disguised_as_a_pdf_is_still_blocked() -> None:
    """Extension, MIME and magic bytes all vote; the worst answer wins."""
    result = extract("invoice.pdf", windows_executable(), "application/pdf")

    assert result.status is ExtractionStatus.BLOCKED
    assert result.kind is AttachmentKind.EXECUTABLE


def test_a_linux_executable_is_blocked_too() -> None:
    result = extract("report.pdf", linux_executable(), "application/pdf")
    assert result.status is ExtractionStatus.BLOCKED


def test_macro_enabled_office_files_are_blocked() -> None:
    """.docm can carry VBA. It is never handed to a parser."""
    for name in ("report.docm", "sheet.xlsm", "deck.pptm"):
        result = extract(name, docx_bytes(), DOCX_MIME)
        assert result.status is ExtractionStatus.BLOCKED, name


@pytest.mark.parametrize(
    "extension", [".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".sh", ".lnk"]
)
def test_every_dangerous_extension_is_blocked(extension: str) -> None:
    result = extract(f"file{extension}", b"anything at all", "application/octet-stream")

    assert result.status is ExtractionStatus.BLOCKED
    assert extension in DANGEROUS_EXTENSIONS


def test_attachment_code_contains_no_execution_primitives() -> None:
    """A structural check: nothing in this package can run a file.

    Parsed with ``ast`` rather than grepped, so a docstring that *mentions*
    subprocess doesn't trip it and an actual call can't hide behind one.
    """
    import ast
    from pathlib import Path

    banned_modules = {"subprocess", "pty", "ctypes", "multiprocessing", "runpy"}
    banned_calls = {"eval", "exec", "compile", "__import__"}
    banned_attributes = {"system", "popen", "spawn", "execv", "execl", "startfile"}

    package = Path(__file__).resolve().parents[2] / "app" / "attachments"
    offences: list[str] = []

    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in banned_modules:
                        offences.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in banned_modules:
                    offences.append(f"{path.name}: from {node.module} import ...")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in banned_calls:
                    offences.append(f"{path.name}: {func.id}()")
                if isinstance(func, ast.Attribute) and func.attr in banned_attributes:
                    offences.append(f"{path.name}: .{func.attr}()")

    assert not offences, offences


def test_pdf_embedded_scripts_are_noted_but_never_run() -> None:
    pdf = pdf_with_text("hello").replace(
        b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript >>"
    )
    result = extract("odd.pdf", pdf, "application/pdf")

    # Whatever the parse outcome, nothing was executed and text is just text.
    assert result.status is not ExtractionStatus.BLOCKED
    assert isinstance(result.text, str)


# --------------------------------------------------------------------
# Hostile and broken input
# --------------------------------------------------------------------


def test_a_decompression_bomb_is_refused() -> None:
    bomb = zip_bomb()
    result = extract("bomb.docx", bomb, DOCX_MIME)

    assert result.status is ExtractionStatus.TOO_LARGE
    assert len(bomb) < 1_000_000  # small archive...
    assert "MB" in result.error or "bomb" in result.error  # ...huge claim


def test_oversized_attachments_are_not_opened() -> None:
    result = extract(
        "huge.pdf", None, "application/pdf", declared_size=MAX_ATTACHMENT_BYTES + 1
    )

    assert result.status is ExtractionStatus.TOO_LARGE
    assert result.text == ""


def test_a_corrupt_pdf_reports_corrupted() -> None:
    result = extract("broken.pdf", b"%PDF-1.4 then nothing useful", "application/pdf")

    assert result.status is ExtractionStatus.CORRUPTED
    assert result.status.is_failure


def test_a_corrupt_docx_reports_corrupted() -> None:
    result = extract("broken.docx", b"PK\x03\x04 not really a docx", DOCX_MIME)

    assert result.status is ExtractionStatus.CORRUPTED


def test_random_bytes_never_crash_the_extractor() -> None:
    import os

    for name in ("a.pdf", "b.docx", "c.csv", "d.txt", "e.unknown"):
        result = extract(name, os.urandom(2048), "application/octet-stream")
        assert result.status is not None
        assert isinstance(result.text, str)


def test_extracted_text_is_capped() -> None:
    result = extract("big.txt", b"x " * 200_000, "text/plain")

    assert len(result.text) <= MAX_EXTRACTED_CHARS
    assert result.truncated


def test_control_characters_are_stripped_from_extracted_text() -> None:
    result = extract("weird.txt", b"hello\x00\x07world", "text/plain")
    assert "\x00" not in result.text


# --------------------------------------------------------------------
# Types we deliberately don't open
# --------------------------------------------------------------------


def test_images_are_recognised_but_not_decoded() -> None:
    result = extract("photo.jpg", b"\xff\xd8\xff\xe0 jpeg bytes", "image/jpeg")

    assert result.kind is AttachmentKind.IMAGE
    assert result.status is ExtractionStatus.UNSUPPORTED
    assert "OCR" in result.error


def test_archives_are_not_opened() -> None:
    result = extract("bundle.zip", b"PK\x03\x04 contents", "application/zip")

    assert result.kind is AttachmentKind.ARCHIVE
    assert result.status is ExtractionStatus.UNSUPPORTED


def test_unknown_types_are_left_alone() -> None:
    result = extract("thing.xyz", b"mystery bytes", "application/octet-stream")

    assert result.kind is AttachmentKind.UNKNOWN
    assert result.status is ExtractionStatus.UNSUPPORTED


# --------------------------------------------------------------------
# Filenames are untrusted
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\config",
        "/absolute/path/file.txt",
        "C:\\Windows\\file.txt",
    ],
)
def test_path_traversal_is_stripped_from_filenames(hostile: str) -> None:
    cleaned = safe_display_name(hostile)

    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert ".." not in cleaned


def test_an_unnamed_attachment_gets_a_placeholder() -> None:
    assert safe_display_name("") == "(unnamed attachment)"
    assert safe_display_name("...") == "(unnamed attachment)"


def test_absurdly_long_filenames_are_trimmed() -> None:
    assert len(safe_display_name("a" * 5000 + ".txt")) <= 180


def test_mismatched_content_is_flagged_as_a_warning() -> None:
    result = extract("document.pdf", b"\x89PNG\r\n\x1a\n image data", "application/pdf")
    assert any("contents look like" in warning for warning in result.warnings)


def test_docx_zip_magic_is_not_flagged_as_a_mismatch() -> None:
    """A .docx legitimately begins with the ZIP signature."""
    result = extract("real.docx", docx_bytes(), DOCX_MIME)
    assert not any("contents look like" in warning for warning in result.warnings)


# --------------------------------------------------------------------
# Classification of kinds
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "mime", "expected"),
    [
        ("a.pdf", "application/pdf", AttachmentKind.PDF),
        ("a.docx", DOCX_MIME, AttachmentKind.DOCX),
        ("a.csv", "text/csv", AttachmentKind.CSV),
        ("a.txt", "text/plain", AttachmentKind.TEXT),
        ("a.png", "image/png", AttachmentKind.IMAGE),
        ("a.zip", "application/zip", AttachmentKind.ARCHIVE),
        ("a.exe", "application/x-msdownload", AttachmentKind.EXECUTABLE),
        ("a.unknown", "application/octet-stream", AttachmentKind.UNKNOWN),
    ],
)
def test_kind_classification(filename, mime, expected) -> None:
    assert classify_attachment(filename, mime) is expected


def test_kind_is_found_from_content_when_the_name_is_useless() -> None:
    assert classify_attachment("noname", "", b"%PDF-1.7 x") is AttachmentKind.PDF
