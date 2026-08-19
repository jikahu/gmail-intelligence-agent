"""Google Sheets + Drive service objects for the control workbook.

Two separate Google APIs are involved:

* **Sheets v4** — reads and writes the rows inside the workbook.
* **Drive v3** — used only to *find* the workbook this app created previously,
  so the workbook survives a Render restart without us having to store its ID
  on an ephemeral filesystem. Because the app holds the narrow ``drive.file``
  scope, a Drive file listing returns **only files this app itself created** —
  the rest of the user's Drive is invisible to us.
"""

from __future__ import annotations

from googleapiclient.discovery import Resource

from app.google_api import build_service

SHEETS_API: tuple[str, str] = ("sheets", "v4")
DRIVE_API: tuple[str, str] = ("drive", "v3")

#: Drive MIME type for a Google Sheets file.
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"


def get_sheets_service() -> Resource:
    """Return an authenticated Sheets v4 service."""
    return build_service(*SHEETS_API)


def get_drive_service() -> Resource:
    """Return an authenticated Drive v3 service (``drive.file`` scope only)."""
    return build_service(*DRIVE_API)


__all__ = (
    "SHEETS_API",
    "DRIVE_API",
    "SPREADSHEET_MIME",
    "get_sheets_service",
    "get_drive_service",
)
