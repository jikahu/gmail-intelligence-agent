"""Phase 2 OAuth scopes — Google Sheets control workbook.

Two scopes are added here:

* ``spreadsheets`` — read and write the workbook. Required because the app
  itself writes the audit log, rule suggestions, and system-run records.
* ``drive.file`` — the *narrow* Drive scope. This does NOT give the app access
  to your Drive. It only grants access to files the app itself creates
  (or that you explicitly open with a Google file-picker built by this app).
  The full-Drive scope (``drive`` / ``drive.readonly``) is intentionally
  never requested.
"""

from __future__ import annotations

from typing import Mapping

#: Read + write the workbook's rows and cells.
SHEETS_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets",
)

#: Narrow Drive scope: only files this app creates.
DRIVE_FILE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/drive.file",
)

#: The exact set of scopes Phase 2 adds, in a stable order.
PHASE_2_SCOPES: tuple[str, ...] = (
    *SHEETS_SCOPES,
    *DRIVE_FILE_SCOPES,
)

SCOPE_DESCRIPTIONS: Mapping[str, str] = {
    "https://www.googleapis.com/auth/spreadsheets": (
        "Read and write Google Sheets — used only for this app's control workbook."
    ),
    "https://www.googleapis.com/auth/drive.file": (
        "Create the control workbook and access ONLY files this app itself creates. "
        "Does NOT grant access to the rest of your Google Drive."
    ),
}


__all__ = (
    "SHEETS_SCOPES",
    "DRIVE_FILE_SCOPES",
    "PHASE_2_SCOPES",
    "SCOPE_DESCRIPTIONS",
)
