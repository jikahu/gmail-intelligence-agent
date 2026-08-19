"""Attachment analysis (CLAUDE.md §11).

**Attachments are read as information, never executed.**

```python
from app.attachments import process_message

report = process_message(message, gmail_client)
report.combined_text     # what the files said
report.as_dict()         # per-file status for the dashboard
```

Nothing in this package runs a file, invokes a macro, or evaluates embedded
script content. Executable and macro-enabled formats are refused before any
parser sees them. And an attachment that can't be read changes nothing about
how its email is classified — the failure is recorded and is otherwise inert.
"""

from app.attachments.extract import extract
from app.attachments.models import (
    AttachmentReport,
    ExtractedAttachment,
    ExtractionStatus,
)
from app.attachments.service import (
    decode_attachment_data,
    download_attachment,
    process_message,
)
from app.attachments.types import AttachmentKind, classify_attachment, safe_display_name

__all__ = (
    "AttachmentKind",
    "AttachmentReport",
    "ExtractedAttachment",
    "ExtractionStatus",
    "classify_attachment",
    "decode_attachment_data",
    "download_attachment",
    "extract",
    "process_message",
    "safe_display_name",
)
