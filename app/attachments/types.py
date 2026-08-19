"""What we will and won't open, and how far (CLAUDE.md §11).

Attachments arrive from strangers. Everything here is written on that
assumption:

* **Nothing is ever executed.** No file is run, no macro is invoked, no
  embedded script is evaluated. The extractors read bytes and return text.
  There is no ``subprocess``, no ``eval``, and no code path that hands a file
  to the operating system.
* **Executable file types are refused outright** rather than parsed. We don't
  need to open a ``.exe`` to know we shouldn't.
* **Every limit below is a hard cap**, because "we'll just read the whole
  thing" is how a 50 KB file turns into 8 GB of memory.

V1 extracts text from PDF, TXT, CSV and DOCX. Images are recognised and
recorded but not decoded — there is no OCR in V1, and image parsers are a
notorious source of memory-corruption bugs, so leaving them unopened is both
the honest and the safer behaviour.
"""

from __future__ import annotations

from enum import Enum

# --------------------------------------------------------------------
# Hard limits
# --------------------------------------------------------------------

#: Largest attachment we will download and open at all.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

#: Largest amount of text kept from any one attachment.
MAX_EXTRACTED_CHARS = 50_000

#: Caps on how much structure we walk before stopping.
MAX_PDF_PAGES = 100
MAX_CSV_ROWS = 1_000
MAX_DOCX_PARAGRAPHS = 5_000

#: Decompression-bomb guards for DOCX (which is a ZIP archive underneath).
#: A file that claims to expand to more than this, or expands by more than
#: this ratio, is refused without being opened.
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class AttachmentKind(str, Enum):
    """What sort of file this is, as far as extraction is concerned."""

    PDF = "pdf"
    DOCX = "docx"
    CSV = "csv"
    TEXT = "text"
    IMAGE = "image"
    ARCHIVE = "archive"
    EXECUTABLE = "executable"
    UNKNOWN = "unknown"


#: Extensions that can run code on the recipient's machine. These are never
#: opened, never parsed, and always flagged. The list is deliberately broad —
#: refusing a harmless file costs nothing.
DANGEROUS_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe", ".com", ".scr", ".pif", ".msi", ".msp", ".cpl", ".dll",
        ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
        ".wsf", ".wsh", ".hta", ".reg", ".lnk", ".inf", ".scf",
        ".jar", ".class", ".apk", ".app", ".dmg", ".pkg",
        ".sh", ".bash", ".zsh", ".run", ".bin", ".elf",
        ".docm", ".xlsm", ".pptm", ".dotm", ".xlam", ".xlsb",
        ".iso", ".img", ".vhd",
    }
)

#: Archive types. Not opened in V1 — unpacking untrusted archives is its own
#: category of risk and buys us nothing for classification.
ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".cab"}
)

TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".text", ".md", ".markdown", ".log", ".rst", ".ics"}
)

CSV_EXTENSIONS: frozenset[str] = frozenset({".csv", ".tsv"})

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".svg"}
)

#: MIME types, checked alongside the extension. Where the two disagree the
#: *more* restrictive answer wins — see :func:`classify_attachment`.
MIME_KINDS: dict[str, AttachmentKind] = {
    "application/pdf": AttachmentKind.PDF,
    "application/x-pdf": AttachmentKind.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": AttachmentKind.DOCX,
    "text/csv": AttachmentKind.CSV,
    "text/tab-separated-values": AttachmentKind.CSV,
    "text/plain": AttachmentKind.TEXT,
    "text/markdown": AttachmentKind.TEXT,
    "text/calendar": AttachmentKind.TEXT,
    "application/zip": AttachmentKind.ARCHIVE,
    "application/x-zip-compressed": AttachmentKind.ARCHIVE,
    "application/x-tar": AttachmentKind.ARCHIVE,
    "application/gzip": AttachmentKind.ARCHIVE,
    "application/x-msdownload": AttachmentKind.EXECUTABLE,
    "application/x-msdos-program": AttachmentKind.EXECUTABLE,
    "application/x-executable": AttachmentKind.EXECUTABLE,
    "application/vnd.ms-word.document.macroenabled.12": AttachmentKind.EXECUTABLE,
    "application/vnd.ms-excel.sheet.macroenabled.12": AttachmentKind.EXECUTABLE,
}

#: File signatures ("magic bytes"). A file whose real content disagrees with
#: its name is not necessarily an attack, but it is worth noticing.
MAGIC_SIGNATURES: tuple[tuple[bytes, AttachmentKind], ...] = (
    (b"%PDF-", AttachmentKind.PDF),
    (b"PK\x03\x04", AttachmentKind.ARCHIVE),  # also DOCX — refined by extension
    (b"MZ", AttachmentKind.EXECUTABLE),       # Windows PE
    (b"\x7fELF", AttachmentKind.EXECUTABLE),  # Linux ELF
    (b"\xff\xd8\xff", AttachmentKind.IMAGE),  # JPEG
    (b"\x89PNG\r\n\x1a\n", AttachmentKind.IMAGE),
    (b"GIF8", AttachmentKind.IMAGE),
)


def extension_of(filename: str) -> str:
    """Return the lowercased final extension, including the dot."""
    name = (filename or "").strip().lower()
    _, dot, tail = name.rpartition(".")
    return f".{tail}" if dot and tail else ""


def safe_display_name(filename: str) -> str:
    """A filename safe to log and show.

    Strips any directory component — attachment names are attacker-controlled
    and have historically been used for path traversal. Nothing in this app
    writes attachments to disk, so this is belt-and-braces, but the name also
    ends up in logs and a spreadsheet.
    """
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    name = name.strip().strip(".")
    return name[:180] or "(unnamed attachment)"


def kind_from_magic(data: bytes) -> AttachmentKind | None:
    """Identify a file from its leading bytes, if we recognise them."""
    if not data:
        return None
    for signature, kind in MAGIC_SIGNATURES:
        if data.startswith(signature):
            return kind
    return None


def classify_attachment(
    filename: str, mime_type: str = "", data: bytes | None = None
) -> AttachmentKind:
    """Work out what an attachment is.

    Three sources of evidence — extension, declared MIME type, and the actual
    leading bytes — and where they disagree, **the most dangerous answer
    wins**. A file called ``invoice.pdf`` that begins with ``MZ`` is a Windows
    executable, and it is treated as one.
    """
    extension = extension_of(filename)
    mime = (mime_type or "").split(";")[0].strip().lower()
    magic = kind_from_magic(data or b"")

    # An executable by any of the three tests is an executable, full stop.
    if (
        extension in DANGEROUS_EXTENSIONS
        or MIME_KINDS.get(mime) is AttachmentKind.EXECUTABLE
        or magic is AttachmentKind.EXECUTABLE
    ):
        return AttachmentKind.EXECUTABLE

    if extension in ARCHIVE_EXTENSIONS:
        return AttachmentKind.ARCHIVE

    # DOCX is a ZIP; the extension is what distinguishes it from a plain
    # archive, so check it before trusting the ZIP magic.
    if extension == ".docx" or MIME_KINDS.get(mime) is AttachmentKind.DOCX:
        return AttachmentKind.DOCX

    if extension == ".pdf" or magic is AttachmentKind.PDF or MIME_KINDS.get(mime) is AttachmentKind.PDF:
        return AttachmentKind.PDF

    if extension in CSV_EXTENSIONS or MIME_KINDS.get(mime) is AttachmentKind.CSV:
        return AttachmentKind.CSV

    if extension in TEXT_EXTENSIONS or MIME_KINDS.get(mime) is AttachmentKind.TEXT:
        return AttachmentKind.TEXT

    if extension in IMAGE_EXTENSIONS or mime.startswith("image/") or magic is AttachmentKind.IMAGE:
        return AttachmentKind.IMAGE

    if magic is AttachmentKind.ARCHIVE:
        return AttachmentKind.ARCHIVE

    return AttachmentKind.UNKNOWN


#: Kinds we attempt to read text from.
EXTRACTABLE_KINDS: frozenset[AttachmentKind] = frozenset(
    {AttachmentKind.PDF, AttachmentKind.DOCX, AttachmentKind.CSV, AttachmentKind.TEXT}
)


__all__ = (
    "ARCHIVE_EXTENSIONS",
    "AttachmentKind",
    "CSV_EXTENSIONS",
    "DANGEROUS_EXTENSIONS",
    "EXTRACTABLE_KINDS",
    "IMAGE_EXTENSIONS",
    "MAX_ATTACHMENT_BYTES",
    "MAX_COMPRESSION_RATIO",
    "MAX_CSV_ROWS",
    "MAX_DOCX_PARAGRAPHS",
    "MAX_EXTRACTED_CHARS",
    "MAX_PDF_PAGES",
    "MAX_UNCOMPRESSED_BYTES",
    "MIME_KINDS",
    "TEXT_EXTENSIONS",
    "classify_attachment",
    "extension_of",
    "kind_from_magic",
    "safe_display_name",
)
