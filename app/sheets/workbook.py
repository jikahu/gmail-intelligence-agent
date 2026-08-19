"""Create and maintain the control workbook.

The user never builds tabs by hand (CLAUDE.md §3). :func:`ensure_workbook` is
the single entry point and is **idempotent** — running it twice changes
nothing the second time. It:

1. Finds the workbook (env override → Drive search → create a new one).
2. Adds any tab declared in :mod:`app.sheets.schema` that doesn't exist yet.
3. Appends any *new* column to the end of an existing tab's header row.
4. Seeds the ``Settings`` tab with safe defaults, but only when it's empty.

**Additive only.** A column that exists in the sheet but not in the schema is
left alone, and columns are never renamed, reordered, or removed. That means a
user who adds their own notes column keeps it, and a user who reorders columns
keeps their order — the repository layer addresses columns by *name*, never by
position.

Finding the workbook through Drive rather than storing its ID locally is
deliberate: Render's filesystem is ephemeral, so a locally-stored ID would be
lost on restart. The narrow ``drive.file`` scope means the Drive search can
only ever see files this app created.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from googleapiclient.discovery import Resource

from app.config import get_settings
from app.logging_config import get_logger
from app.sheets.client import (
    SPREADSHEET_MIME,
    get_drive_service,
    get_sheets_service,
)
from app.sheets.schema import (
    DEFAULT_SETTINGS,
    SETTINGS_TAB,
    WORKBOOK_TABS,
    Tab,
)

log = get_logger("app.sheets.workbook")

#: Title of the spreadsheet created in the user's Drive.
WORKBOOK_NAME = "Gmail Agent Control Workbook"


@dataclass
class WorkbookInfo:
    """What :func:`ensure_workbook` found or changed."""

    spreadsheet_id: str
    url: str
    created: bool = False
    tabs_created: list[str] = field(default_factory=list)
    columns_added: dict[str, list[str]] = field(default_factory=dict)
    settings_seeded: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.created
            or self.tabs_created
            or self.columns_added
            or self.settings_seeded
        )


def workbook_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def column_letter(index: int) -> str:
    """Return the A1 column label for a **zero-based** column index.

    ``0 -> "A"``, ``25 -> "Z"``, ``26 -> "AA"``.
    """
    if index < 0:
        raise ValueError("Column index must be zero or greater.")
    label = ""
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def quote_tab(name: str) -> str:
    """Quote a tab name for use in an A1 range (tab names may contain spaces)."""
    escaped = name.replace("'", "''")
    return f"'{escaped}'"


# --------------------------------------------------------------------
# Finding the workbook
# --------------------------------------------------------------------


def find_workbook_id(drive: Resource) -> str | None:
    """Search Drive for a workbook this app created previously.

    Returns ``None`` if there isn't one. With the ``drive.file`` scope this
    listing is restricted to files this app itself created.
    """
    escaped = WORKBOOK_NAME.replace("'", "\\'")
    response = (
        drive.files()
        .list(
            q=(
                f"mimeType='{SPREADSHEET_MIME}' "
                f"and name='{escaped}' "
                "and trashed=false"
            ),
            spaces="drive",
            fields="files(id,name)",
            pageSize=10,
        )
        .execute()
    )
    files = response.get("files") or []
    if not files:
        return None
    if len(files) > 1:
        log.warning(
            "multiple_control_workbooks_found",
            extra={"count": len(files), "using": files[0]["id"]},
        )
    return files[0]["id"]


def _create_workbook(sheets: Resource) -> str:
    """Create the workbook with every tab from the schema. Returns its ID."""
    body = {
        "properties": {"title": WORKBOOK_NAME},
        "sheets": [
            {
                "properties": {
                    "title": tab.name,
                    "gridProperties": {"frozenRowCount": 1},
                }
            }
            for tab in WORKBOOK_TABS
        ],
    }
    created = (
        sheets.spreadsheets()
        .create(body=body, fields="spreadsheetId")
        .execute()
    )
    spreadsheet_id = created["spreadsheetId"]
    log.info("control_workbook_created", extra={"spreadsheet_id": spreadsheet_id})
    return spreadsheet_id


# --------------------------------------------------------------------
# Tabs and headers
# --------------------------------------------------------------------


def _existing_sheet_properties(sheets: Resource, spreadsheet_id: str) -> dict[str, int]:
    """Return ``{tab_title: sheetId}`` for every tab in the workbook."""
    meta = (
        sheets.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)")
        .execute()
    )
    return {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets") or []
    }


def _add_missing_tabs(
    sheets: Resource, spreadsheet_id: str, missing: list[str]
) -> None:
    if not missing:
        return
    requests = [
        {
            "addSheet": {
                "properties": {
                    "title": name,
                    "gridProperties": {"frozenRowCount": 1},
                }
            }
        }
        for name in missing
    ]
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()
    log.info("control_workbook_tabs_added", extra={"tabs": missing})


def _read_header_rows(
    sheets: Resource, spreadsheet_id: str, tabs: tuple[Tab, ...]
) -> dict[str, list[str]]:
    """Read row 1 of each tab in a single batch call."""
    if not tabs:
        return {}
    ranges = [f"{quote_tab(tab.name)}!1:1" for tab in tabs]
    response = (
        sheets.spreadsheets()
        .values()
        .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
        .execute()
    )
    headers: dict[str, list[str]] = {}
    for tab, value_range in zip(tabs, response.get("valueRanges") or []):
        rows = value_range.get("values") or []
        headers[tab.name] = [str(c).strip() for c in (rows[0] if rows else [])]
    return headers


def _reconcile_headers(
    sheets: Resource,
    spreadsheet_id: str,
    existing_headers: dict[str, list[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Write header rows, appending any column the schema gained.

    Returns ``({tab_name: [newly_added_columns]}, {tab_name: final_header})``.
    """
    updates: list[dict[str, object]] = []
    added: dict[str, list[str]] = {}
    final_headers: dict[str, list[str]] = {}

    for tab in WORKBOOK_TABS:
        existing = existing_headers.get(tab.name) or []
        new_columns = [c for c in tab.column_names if c not in existing]

        # Preserve whatever order the sheet already uses; append new columns.
        final_header = existing + new_columns
        final_headers[tab.name] = final_header

        if existing and not new_columns:
            continue  # Header already correct — leave the user's layout alone.

        updates.append(
            {
                "range": f"{quote_tab(tab.name)}!A1",
                "values": [final_header],
            }
        )
        if existing and new_columns:
            added[tab.name] = new_columns

    if updates:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
    if added:
        log.info("control_workbook_columns_added", extra={"columns": added})
    return added, final_headers


def _style_header_rows(
    sheets: Resource, spreadsheet_id: str, sheet_ids: dict[str, int]
) -> None:
    """Bold row 1 and freeze it. Best-effort — never fatal."""
    requests: list[dict[str, object]] = []
    for tab in WORKBOOK_TABS:
        sheet_id = sheet_ids.get(tab.name)
        if sheet_id is None:
            continue
        requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {"textFormat": {"bold": True}}
                    },
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            }
        )
    if not requests:
        return
    try:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()
    except Exception as exc:  # noqa: BLE001 — cosmetic only
        log.warning("control_workbook_header_styling_failed", extra={"error": str(exc)})


# --------------------------------------------------------------------
# Settings seeding
# --------------------------------------------------------------------


def _settings_is_empty(sheets: Resource, spreadsheet_id: str) -> bool:
    """True when the Settings tab has a header but no data rows."""
    last_col = column_letter(len(SETTINGS_TAB.columns) - 1)
    response = (
        sheets.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"{quote_tab(SETTINGS_TAB.name)}!A2:{last_col}",
        )
        .execute()
    )
    rows = response.get("values") or []
    return not any(any(str(cell).strip() for cell in row) for row in rows)


def _seed_default_settings(
    sheets: Resource, spreadsheet_id: str, header: list[str]
) -> None:
    """Write the default settings rows, laid out to match ``header``.

    The header is whatever the sheet actually has — the user may have reordered
    or added columns — so rows are built by column *name*, never by position.
    """
    now = _now_iso()
    header = header or list(SETTINGS_TAB.column_names)
    values = [
        [
            {
                "key": key,
                "value": value,
                "description": description,
                "updated_at": now,
            }.get(column, "")
            for column in header
        ]
        for key, value, description in DEFAULT_SETTINGS
    ]
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_tab(SETTINGS_TAB.name)}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    log.info("control_workbook_settings_seeded", extra={"count": len(values)})


# --------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------


def ensure_workbook(
    sheets: Resource | None = None,
    drive: Resource | None = None,
    spreadsheet_id: str | None = None,
) -> WorkbookInfo:
    """Find or create the control workbook and bring it up to schema.

    Safe to call on every boot. The service arguments exist so tests can pass
    fakes; in production they're built from the stored OAuth token.
    """
    sheets = sheets or get_sheets_service()
    drive = drive or get_drive_service()

    configured = spreadsheet_id or get_settings().sheets_workbook_id
    resolved = configured or find_workbook_id(drive)

    created = False
    if not resolved:
        resolved = _create_workbook(sheets)
        created = True

    info = WorkbookInfo(
        spreadsheet_id=resolved, url=workbook_url(resolved), created=created
    )

    sheet_ids = _existing_sheet_properties(sheets, resolved)
    missing_tabs = [t.name for t in WORKBOOK_TABS if t.name not in sheet_ids]
    if missing_tabs:
        _add_missing_tabs(sheets, resolved, missing_tabs)
        info.tabs_created = missing_tabs
        sheet_ids = _existing_sheet_properties(sheets, resolved)

    existing_headers = _read_header_rows(sheets, resolved, WORKBOOK_TABS)
    info.columns_added, final_headers = _reconcile_headers(
        sheets, resolved, existing_headers
    )

    if _settings_is_empty(sheets, resolved):
        _seed_default_settings(
            sheets, resolved, final_headers.get(SETTINGS_TAB.name, [])
        )
        info.settings_seeded = True

    _style_header_rows(sheets, resolved, sheet_ids)

    # NOTE: keys here must not collide with reserved LogRecord attributes
    # (``created``, ``module``, ``name``, ``args``, …) — logging raises if they do.
    log.info(
        "control_workbook_ready",
        extra={
            "spreadsheet_id": resolved,
            "workbook_created": info.created,
            "tabs_created": info.tabs_created,
            "settings_seeded": info.settings_seeded,
        },
    )
    return info


__all__ = (
    "WORKBOOK_NAME",
    "WorkbookInfo",
    "ensure_workbook",
    "find_workbook_id",
    "workbook_url",
    "column_letter",
    "quote_tab",
)
