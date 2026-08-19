"""In-memory fakes for the Google Sheets v4 and Drive v3 APIs.

Enough of the real behaviour to test the workbook and repository layers without
network access or credentials, including the two quirks that bite in
production:

* Sheets **trims trailing empty cells and rows** — a row written as
  ``["a", "", ""]`` reads back as ``["a"]``. Code that indexes by position
  without padding breaks against the real API; this fake reproduces that.
* ``values().append`` inserts *after the last non-empty row*, not at the
  literal range given.
"""

from __future__ import annotations

import re
from typing import Any

_REF_RE = re.compile(r"^(?P<col>[A-Z]*)(?P<row>\d*)$")


def column_index(label: str) -> int:
    """``"A" -> 0``, ``"Z" -> 25``, ``"AA" -> 26``."""
    total = 0
    for char in label:
        total = total * 26 + (ord(char) - ord("A") + 1)
    return total - 1


def column_label(index: int) -> str:
    """Inverse of :func:`column_index`."""
    label = ""
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def parse_range(a1: str) -> tuple[str | None, int, int | None, int, int | None]:
    """Split an A1 range into ``(tab, start_row, end_row, start_col, end_col)``.

    Rows/cols are zero-based; ``None`` for an end bound means "unbounded".
    """
    tab: str | None = None
    ref = a1
    if "!" in a1:
        raw_tab, ref = a1.rsplit("!", 1)
        if raw_tab.startswith("'") and raw_tab.endswith("'"):
            raw_tab = raw_tab[1:-1].replace("''", "'")
        tab = raw_tab

    if not ref:
        return tab, 0, None, 0, None

    parts = ref.split(":")
    start = _REF_RE.match(parts[0].upper())
    if start is None:
        raise ValueError(f"Unparseable A1 range: {a1!r}")

    start_col = column_index(start["col"]) if start["col"] else 0
    start_row = int(start["row"]) - 1 if start["row"] else 0

    if len(parts) == 1:
        # A bare anchor like "A1" — treat as an unbounded write target.
        return tab, start_row, None, start_col, None

    end = _REF_RE.match(parts[1].upper())
    if end is None:
        raise ValueError(f"Unparseable A1 range: {a1!r}")
    end_col = column_index(end["col"]) if end["col"] else None
    end_row = int(end["row"]) - 1 if end["row"] else None
    return tab, start_row, end_row, start_col, end_col


def _trim_row(row: list[str]) -> list[str]:
    out = list(row)
    while out and str(out[-1]) == "":
        out.pop()
    return out


def _trim_grid(rows: list[list[str]]) -> list[list[str]]:
    trimmed = [_trim_row(r) for r in rows]
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed


class _Executable:
    """Mimics the googleapiclient ``.execute()`` deferred-call style."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self, **_kwargs: Any) -> Any:
        return self._result


class FakeSpreadsheet:
    def __init__(self, spreadsheet_id: str, title: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.title = title
        self.grids: dict[str, list[list[str]]] = {}
        self.sheet_ids: dict[str, int] = {}
        self._next_sheet_id = 100

    def add_sheet(self, title: str) -> int:
        if title in self.sheet_ids:
            return self.sheet_ids[title]
        sheet_id = self._next_sheet_id
        self._next_sheet_id += 1
        self.sheet_ids[title] = sheet_id
        self.grids[title] = []
        return sheet_id

    def grid(self, title: str) -> list[list[str]]:
        if title not in self.grids:
            raise KeyError(f"Unable to parse range: {title}")
        return self.grids[title]

    def write(self, a1: str, values: list[list[Any]]) -> None:
        tab, start_row, _end_row, start_col, _end_col = parse_range(a1)
        assert tab is not None, "Fake requires a tab-qualified range"
        grid = self.grid(tab)
        for r_offset, row in enumerate(values):
            r = start_row + r_offset
            while len(grid) <= r:
                grid.append([])
            target = grid[r]
            for c_offset, value in enumerate(row):
                c = start_col + c_offset
                while len(target) <= c:
                    target.append("")
                target[c] = "" if value is None else str(value)

    def read(self, a1: str) -> list[list[str]]:
        tab, start_row, end_row, start_col, end_col = parse_range(a1)
        assert tab is not None, "Fake requires a tab-qualified range"
        grid = self.grid(tab)
        last_row = len(grid) if end_row is None else min(end_row + 1, len(grid))
        window: list[list[str]] = []
        for row in grid[start_row:last_row]:
            last_col = len(row) if end_col is None else min(end_col + 1, len(row))
            window.append(list(row[start_col:last_col]))
        return _trim_grid(window)

    def append(self, a1: str, values: list[list[Any]]) -> None:
        tab, _start_row, _end_row, start_col, _end_col = parse_range(a1)
        assert tab is not None
        grid = self.grid(tab)
        # Real behaviour: land after the last row that has any content.
        next_row = len(_trim_grid(grid))
        anchor = f"'{tab}'!{column_label(start_col)}{next_row + 1}"
        self.write(anchor, values)


class FakeSheetsService:
    """Stands in for ``build("sheets", "v4")``."""

    def __init__(self) -> None:
        self.spreadsheets_by_id: dict[str, FakeSpreadsheet] = {}
        self.batch_update_requests: list[dict[str, Any]] = []
        self.call_counts: dict[str, int] = {}
        self._next_id = 1

    # -- bookkeeping -------------------------------------------------

    def _count(self, name: str) -> None:
        self.call_counts[name] = self.call_counts.get(name, 0) + 1

    def _get(self, spreadsheet_id: str) -> FakeSpreadsheet:
        if spreadsheet_id not in self.spreadsheets_by_id:
            raise KeyError(f"Requested entity was not found: {spreadsheet_id}")
        return self.spreadsheets_by_id[spreadsheet_id]

    def seed(self, title: str = "Gmail Agent Control Workbook") -> FakeSpreadsheet:
        """Create an empty spreadsheet directly (bypassing the API surface)."""
        spreadsheet_id = f"sheet-{self._next_id}"
        self._next_id += 1
        book = FakeSpreadsheet(spreadsheet_id, title)
        self.spreadsheets_by_id[spreadsheet_id] = book
        return book

    # -- API surface -------------------------------------------------

    def spreadsheets(self) -> "_FakeSpreadsheetsResource":
        return _FakeSpreadsheetsResource(self)


class _FakeSpreadsheetsResource:
    def __init__(self, service: FakeSheetsService) -> None:
        self._service = service

    def create(self, body: dict[str, Any], fields: str | None = None) -> _Executable:
        self._service._count("create")
        title = body.get("properties", {}).get("title", "Untitled")
        book = self._service.seed(title)
        for sheet in body.get("sheets") or []:
            book.add_sheet(sheet["properties"]["title"])
        return _Executable(
            {
                "spreadsheetId": book.spreadsheet_id,
                "spreadsheetUrl": (
                    f"https://docs.google.com/spreadsheets/d/{book.spreadsheet_id}/edit"
                ),
            }
        )

    def get(self, spreadsheetId: str, fields: str | None = None) -> _Executable:
        self._service._count("get")
        book = self._service._get(spreadsheetId)
        return _Executable(
            {
                "sheets": [
                    {"properties": {"sheetId": sheet_id, "title": title}}
                    for title, sheet_id in book.sheet_ids.items()
                ]
            }
        )

    def batchUpdate(self, spreadsheetId: str, body: dict[str, Any]) -> _Executable:
        self._service._count("batchUpdate")
        book = self._service._get(spreadsheetId)
        replies: list[dict[str, Any]] = []
        for request in body.get("requests") or []:
            self._service.batch_update_requests.append(request)
            if "addSheet" in request:
                title = request["addSheet"]["properties"]["title"]
                sheet_id = book.add_sheet(title)
                replies.append({"addSheet": {"properties": {"sheetId": sheet_id}}})
            else:
                replies.append({})
        return _Executable({"replies": replies})

    def values(self) -> "_FakeValuesResource":
        return _FakeValuesResource(self._service)


class _FakeValuesResource:
    def __init__(self, service: FakeSheetsService) -> None:
        self._service = service

    def get(self, spreadsheetId: str, range: str) -> _Executable:  # noqa: A002
        self._service._count("values.get")
        book = self._service._get(spreadsheetId)
        return _Executable({"range": range, "values": book.read(range)})

    def batchGet(self, spreadsheetId: str, ranges: list[str]) -> _Executable:
        self._service._count("values.batchGet")
        book = self._service._get(spreadsheetId)
        return _Executable(
            {
                "valueRanges": [
                    {"range": r, "values": book.read(r)} for r in ranges
                ]
            }
        )

    def batchUpdate(self, spreadsheetId: str, body: dict[str, Any]) -> _Executable:
        self._service._count("values.batchUpdate")
        book = self._service._get(spreadsheetId)
        for entry in body.get("data") or []:
            book.write(entry["range"], entry["values"])
        return _Executable({"totalUpdatedSheets": len(body.get("data") or [])})

    def update(
        self,
        spreadsheetId: str,
        range: str,  # noqa: A002
        valueInputOption: str,
        body: dict[str, Any],
    ) -> _Executable:
        self._service._count("values.update")
        book = self._service._get(spreadsheetId)
        book.write(range, body["values"])
        return _Executable({"updatedRows": len(body["values"])})

    def append(
        self,
        spreadsheetId: str,
        range: str,  # noqa: A002
        valueInputOption: str,
        body: dict[str, Any],
        insertDataOption: str | None = None,
    ) -> _Executable:
        self._service._count("values.append")
        book = self._service._get(spreadsheetId)
        book.append(range, body["values"])
        return _Executable({"updates": {"updatedRows": len(body["values"])}})


class FakeDriveService:
    """Stands in for ``build("drive", "v3")`` — only ``files().list`` is used."""

    def __init__(self, files: list[dict[str, str]] | None = None) -> None:
        self.files_present: list[dict[str, str]] = list(files or [])
        self.queries: list[str] = []

    def files(self) -> "_FakeFilesResource":
        return _FakeFilesResource(self)


class _FakeFilesResource:
    def __init__(self, service: FakeDriveService) -> None:
        self._service = service

    def list(
        self,
        q: str | None = None,
        spaces: str | None = None,
        fields: str | None = None,
        pageSize: int | None = None,
    ) -> _Executable:
        self._service.queries.append(q or "")
        return _Executable({"files": list(self._service.files_present)})


__all__ = (
    "FakeDriveService",
    "FakeSheetsService",
    "FakeSpreadsheet",
    "column_index",
    "column_label",
    "parse_range",
)
