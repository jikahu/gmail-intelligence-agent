"""Repository layer over the control workbook.

The rest of the app must never know it is talking to Google Sheets. It asks for
"the active sender rules" and gets back plain Python objects. Swapping Sheets
for Postgres later means rewriting this file and nothing else (CLAUDE.md §17).

Two rules make that possible:

* **Columns are addressed by name, never by cell coordinate.** If the user
  drags a column to a new position or inserts one of their own, nothing breaks.
* **Reads are cached briefly** (:data:`CACHE_TTL_SECONDS`) so a run that
  classifies 200 emails doesn't make 200 identical Sheets API calls. Any write
  through this layer clears the cache for that tab.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping

from googleapiclient.discovery import Resource

from app.logging_config import get_logger
from app.sheets.client import get_sheets_service
from app.sheets.schema import (
    AUDIT_LOG_TAB,
    DEADLINES_TAB,
    DIGEST_LOG_TAB,
    DOMAIN_RULES_TAB,
    LEARNED_RULE_SUGGESTIONS_TAB,
    REVIEW_FEEDBACK_TAB,
    SENDER_RULES_TAB,
    SETTINGS_TAB,
    SUBSCRIPTIONS_TAB,
    SYSTEM_RUNS_TAB,
    TRIPS_TAB,
    VIPS_TAB,
    Tab,
    tab_by_name,
)
from app.sheets.workbook import column_letter, quote_tab

log = get_logger("app.sheets.repository")

#: How long a tab's rows stay cached in memory before the next read refetches.
CACHE_TTL_SECONDS: float = 30.0

_TRUE_VALUES = frozenset({"true", "yes", "y", "1", "on"})
_FALSE_VALUES = frozenset({"false", "no", "n", "0", "off"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Row:
    """One data row, keyed by column name.

    ``number`` is the 1-based sheet row (row 1 is the header, so data starts
    at 2). It's what :meth:`SheetTable.update` needs to write the row back.
    """

    number: int
    values: dict[str, str] = field(default_factory=dict)

    def get(self, column: str, default: str = "") -> str:
        return self.values.get(column, default) or default

    def __getitem__(self, column: str) -> str:
        return self.values[column]

    def __contains__(self, column: str) -> bool:
        return column in self.values


class SheetTable:
    """Row-oriented access to a single tab."""

    def __init__(self, tab: Tab, spreadsheet_id: str, sheets: Resource) -> None:
        self._tab = tab
        self._spreadsheet_id = spreadsheet_id
        self._sheets = sheets
        self._cache: tuple[float, list[str], list[Row]] | None = None
        #: The header row, cached for this table's whole lifetime (not just
        #: CACHE_TTL_SECONDS). Column names only change via a user edit or a
        #: fresh POST /sheets/init, never mid-run, so unlike row data there's
        #: no correctness reason to refetch it on every write.
        self._header_cache: list[str] | None = None

    @property
    def name(self) -> str:
        return self._tab.name

    # -------- Reads --------

    def _fetch(self) -> tuple[list[str], list[Row]]:
        last_col = column_letter(max(len(self._tab.columns), 1) + 25)
        response = (
            self._sheets.spreadsheets()
            .values()
            .get(
                spreadsheetId=self._spreadsheet_id,
                range=f"{quote_tab(self._tab.name)}!A:{last_col}",
            )
            .execute()
        )
        raw = response.get("values") or []
        if not raw:
            return [], []

        header = [str(c).strip() for c in raw[0]]
        rows: list[Row] = []
        for offset, raw_row in enumerate(raw[1:], start=2):
            if not any(str(cell).strip() for cell in raw_row):
                continue  # Skip blank spacer rows the user may have left.
            padded = list(raw_row) + [""] * (len(header) - len(raw_row))
            values = {
                name: str(padded[i]).strip()
                for i, name in enumerate(header)
                if name
            }
            rows.append(Row(number=offset, values=values))
        return header, rows

    def _load(self) -> tuple[list[str], list[Row]]:
        now = time.monotonic()
        if self._cache is not None and (now - self._cache[0]) < CACHE_TTL_SECONDS:
            return self._cache[1], self._cache[2]
        header, rows = self._fetch()
        self._cache = (now, header, rows)
        return header, rows

    def header(self) -> list[str]:
        return list(self._load()[0])

    def _cached_header(self) -> list[str]:
        """Header for write paths — fetched once, not on every append/update.

        A write-heavy run (e.g. a 250-message acceptance run writing one
        Audit_Log row per message) must not turn into 250 reads just to look
        up column names each time; that alone is enough to trip Sheets API's
        per-minute read quota (CLAUDE.md §17: "cache to avoid excessive
        Sheets API calls").
        """
        if self._header_cache is None:
            header, _ = self._load()
            self._header_cache = header
        return self._header_cache

    def rows(self) -> list[Row]:
        return list(self._load()[1])

    def __iter__(self) -> Iterator[Row]:
        return iter(self.rows())

    def find(self, **equals: str) -> list[Row]:
        """Return rows where every ``column=value`` pair matches (case-insensitive)."""
        wanted = {k: (v or "").strip().lower() for k, v in equals.items()}
        return [
            row
            for row in self.rows()
            if all(row.get(col).lower() == val for col, val in wanted.items())
        ]

    def first(self, **equals: str) -> Row | None:
        matches = self.find(**equals)
        return matches[0] if matches else None

    def invalidate(self) -> None:
        self._cache = None

    # -------- Writes --------

    def _row_in_header_order(
        self, header: list[str], values: Mapping[str, Any], base: Row | None = None
    ) -> list[str]:
        out: list[str] = []
        for column in header:
            if column in values:
                out.append("" if values[column] is None else str(values[column]))
            elif base is not None:
                out.append(base.get(column))
            else:
                out.append("")
        return out

    def append(self, values: Mapping[str, Any]) -> None:
        """Append one row. Unknown column names are ignored, not invented."""
        header = self._cached_header()
        if not header:
            raise RuntimeError(
                f"Tab {self._tab.name!r} has no header row. Run workbook "
                "initialization (POST /sheets/init) first."
            )
        unknown = set(values) - set(header)
        if unknown:
            log.warning(
                "sheet_append_unknown_columns",
                extra={"tab": self._tab.name, "unknown": sorted(unknown)},
            )
        self._sheets.spreadsheets().values().append(
            spreadsheetId=self._spreadsheet_id,
            range=f"{quote_tab(self._tab.name)}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [self._row_in_header_order(header, values)]},
        ).execute()
        self.invalidate()

    def append_many(self, rows: list[Mapping[str, Any]]) -> None:
        """Append many rows in one API call.

        A batch run (e.g. a 250-message acceptance run writing one Audit_Log
        row per message) must not turn into 250 separate write calls — Sheets
        API's write quota is 60 requests/minute/user, so anything sized like
        a real run needs one call for the whole batch, not one per row
        (CLAUDE.md §17: "cache to avoid excessive Sheets API calls").
        """
        if not rows:
            return
        header = self._cached_header()
        if not header:
            raise RuntimeError(
                f"Tab {self._tab.name!r} has no header row. Run workbook "
                "initialization (POST /sheets/init) first."
            )
        unknown = {key for values in rows for key in values} - set(header)
        if unknown:
            log.warning(
                "sheet_append_unknown_columns",
                extra={"tab": self._tab.name, "unknown": sorted(unknown)},
            )
        self._sheets.spreadsheets().values().append(
            spreadsheetId=self._spreadsheet_id,
            range=f"{quote_tab(self._tab.name)}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={
                "values": [
                    self._row_in_header_order(header, values) for values in rows
                ]
            },
        ).execute()
        self.invalidate()

    def update(self, row: Row, values: Mapping[str, Any]) -> None:
        """Overwrite the named columns of an existing row, leaving the rest."""
        header = self._cached_header()
        last_col = column_letter(len(header) - 1)
        self._sheets.spreadsheets().values().update(
            spreadsheetId=self._spreadsheet_id,
            range=(
                f"{quote_tab(self._tab.name)}!A{row.number}:{last_col}{row.number}"
            ),
            valueInputOption="RAW",
            body={"values": [self._row_in_header_order(header, values, base=row)]},
        ).execute()
        self.invalidate()


# --------------------------------------------------------------------
# Typed rows
# --------------------------------------------------------------------


@dataclass(frozen=True)
class SenderRule:
    sender: str
    rule_type: str
    action: str
    status: str
    source: str
    notes: str = ""


@dataclass(frozen=True)
class DomainRule:
    domain: str
    rule_type: str
    action: str
    status: str
    source: str
    notes: str = ""


@dataclass(frozen=True)
class VIP:
    email: str
    name: str
    status: str
    notes: str = ""


# --------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------


class SettingsRepository:
    """The user-editable control panel (``Settings`` tab).

    Values are stored as strings because that's what a spreadsheet cell is;
    the typed getters below do the conversion so callers never parse by hand.
    """

    def __init__(self, table: SheetTable) -> None:
        self._table = table

    def all(self) -> dict[str, str]:
        return {row.get("key"): row.get("value") for row in self._table if row.get("key")}

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._table.first(key=key)
        if row is None:
            return default
        value = row.get("value")
        return value if value != "" else default

    def get_bool(self, key: str, default: bool) -> bool:
        raw = (self.get(key) or "").strip().lower()
        if raw in _TRUE_VALUES:
            return True
        if raw in _FALSE_VALUES:
            return False
        return default

    def get_int(self, key: str, default: int) -> int:
        try:
            return int((self.get(key) or "").strip())
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float) -> float:
        try:
            return float((self.get(key) or "").strip())
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: Any, description: str | None = None) -> None:
        """Update an existing setting, or add it if the key is new."""
        payload: dict[str, Any] = {
            "key": key,
            "value": "" if value is None else str(value),
            "updated_at": _now_iso(),
        }
        existing = self._table.first(key=key)
        if existing is None:
            payload["description"] = description or ""
            self._table.append(payload)
            return
        if description is not None:
            payload["description"] = description
        self._table.update(existing, payload)


class RulesRepository:
    """Sender rules, domain rules, and learned-rule suggestions.

    Interface intentionally matches the sketch in CLAUDE.md §17.
    """

    def __init__(
        self,
        sender_table: SheetTable,
        domain_table: SheetTable,
        suggestions_table: SheetTable,
    ) -> None:
        self._senders = sender_table
        self._domains = domain_table
        self._suggestions = suggestions_table

    def get_sender_rules(self, status: str | None = "active") -> list[SenderRule]:
        rows = self._senders.find(status=status) if status else self._senders.rows()
        return [
            SenderRule(
                sender=row.get("sender").lower(),
                rule_type=row.get("rule_type"),
                action=row.get("action"),
                status=row.get("status"),
                source=row.get("source"),
                notes=row.get("notes"),
            )
            for row in rows
            if row.get("sender")
        ]

    def get_domain_rules(self, status: str | None = "active") -> list[DomainRule]:
        rows = self._domains.find(status=status) if status else self._domains.rows()
        return [
            DomainRule(
                domain=row.get("domain").lower().lstrip("@"),
                rule_type=row.get("rule_type"),
                action=row.get("action"),
                status=row.get("status"),
                source=row.get("source"),
                notes=row.get("notes"),
            )
            for row in rows
            if row.get("domain")
        ]

    def add_rule_suggestion(
        self,
        target: str,
        suggested_rule: str,
        evidence: str,
        confidence: float,
    ) -> str:
        """Record a rule *suggestion*. Suggestions are never auto-applied.

        Returns the generated ``suggestion_id``.
        """
        suggestion_id = uuid.uuid4().hex[:12]
        self._suggestions.append(
            {
                "suggestion_id": suggestion_id,
                "target": target,
                "suggested_rule": suggested_rule,
                "evidence": evidence,
                "confidence": f"{confidence:.2f}",
                "status": "pending",
                "created_at": _now_iso(),
                "approved_at": "",
            }
        )
        log.info(
            "rule_suggestion_added",
            extra={"suggestion_id": suggestion_id, "target": target},
        )
        return suggestion_id

    def pending_suggestions(self) -> list[Row]:
        return self._suggestions.find(status="pending")

    def approved_suggestions(self) -> list[Row]:
        """Suggestions the user has explicitly flipped to ``approved`` in the sheet.

        Approving a suggestion here does not, by itself, change classification —
        :func:`app.learning.service.promote_approved_suggestions` is the
        explicit, separately-triggered step that turns one of these into a
        real Sender_Rules/Domain_Rules row (CLAUDE.md §11: never silently).
        """
        return self._suggestions.find(status="approved")

    def add_sender_rule(
        self,
        sender: str,
        rule_type: str = "whitelist",
        action: str = "",
        source: str = "learned",
        notes: str = "",
    ) -> None:
        """Create or update an active sender rule. Idempotent on ``sender``."""
        _KeyedTable(self._senders, ("sender",)).upsert(
            {
                "sender": (sender or "").strip().lower(),
                "rule_type": rule_type,
                "action": action,
                "status": "active",
                "source": source,
                "approved_at": _now_iso(),
                "notes": notes,
            }
        )

    def add_domain_rule(
        self,
        domain: str,
        rule_type: str = "whitelist",
        action: str = "",
        source: str = "learned",
        notes: str = "",
    ) -> None:
        """Create or update an active domain rule. Idempotent on ``domain``.

        Does **not** itself refuse public mailbox providers — that guard lives
        in :func:`app.classification.context.build_rule`, which silently
        ignores such a row at classification time either way (CLAUDE.md §8).
        Callers that want to tell the user *why* up front should check first.
        """
        _KeyedTable(self._domains, ("domain",)).upsert(
            {
                "domain": (domain or "").strip().lower().lstrip("@"),
                "rule_type": rule_type,
                "action": action,
                "status": "active",
                "source": source,
                "approved_at": _now_iso(),
                "notes": notes,
            }
        )


class VIPRepository:
    """Approved VIP senders. Approval is always explicit (CLAUDE.md §8)."""

    def __init__(self, table: SheetTable) -> None:
        self._table = table

    def approved(self) -> list[VIP]:
        return [
            VIP(
                email=row.get("email").lower(),
                name=row.get("name"),
                status=row.get("status"),
                notes=row.get("notes"),
            )
            for row in self._table.find(status="approved")
            if row.get("email")
        ]

    def approved_emails(self) -> set[str]:
        return {vip.email for vip in self.approved()}

    def suggested(self) -> list[VIP]:
        """VIP *suggestions* awaiting the user's approval (status ``pending``).

        The dashboard's "VIP Suggestions" card reads these. The suggestions
        themselves are generated by the learning layer in Phase 9; until then
        this is normally empty.
        """
        return [
            VIP(
                email=row.get("email").lower(),
                name=row.get("name"),
                status=row.get("status"),
                notes=row.get("notes"),
            )
            for row in self._table.find(status="pending")
            if row.get("email")
        ]

    def is_vip(self, email: str) -> bool:
        return (email or "").strip().lower() in self.approved_emails()

    def suggest(self, email: str, name: str = "", notes: str = "") -> None:
        """Add a VIP *suggestion*. Status is ``pending`` until the user approves."""
        if self._table.first(email=email) is not None:
            return
        self._table.append(
            {
                "email": email.strip().lower(),
                "name": name,
                "status": "pending",
                "approved_at": "",
                "notes": notes,
            }
        )


class ReviewFeedbackRepository:
    """The ``Review_Feedback`` tab — one row per dashboard action on a Review row.

    Every Keep / Review-Correct / Make-Sender-Rule / Make-Domain-Rule /
    Suggest-VIP click lands here, whether or not it also produced a rule
    suggestion (CLAUDE.md §12). This is a plain append-only log, not a keyed
    table — the same message can collect feedback more than once.
    """

    def __init__(self, table: SheetTable) -> None:
        self._table = table

    def record(
        self,
        *,
        gmail_message_id: str,
        thread_id: str,
        original_classification: str,
        original_reason: str,
        user_decision: str,
        resulting_rule_suggestion: str = "",
    ) -> None:
        self._table.append(
            {
                "gmail_message_id": gmail_message_id,
                "thread_id": thread_id,
                "original_classification": original_classification,
                "original_reason": original_reason,
                "user_decision": user_decision,
                "resulting_rule_suggestion": resulting_rule_suggestion,
                "timestamp": _now_iso(),
            }
        )

    def for_message(self, gmail_message_id: str) -> list[Row]:
        return self._table.find(gmail_message_id=gmail_message_id)

    def all(self) -> list[Row]:
        return self._table.rows()


class AuditRepository:
    """The ``Audit_Log`` tab — every automated and manual action (CLAUDE.md §13).

    Append-only by design: an audit trail that could be edited in place
    wouldn't be trustworthy as the substrate for the future Undo Last Run
    (Phase 12). Rows are written through :func:`app.audit.service`, which
    knows how to turn a classification decision or a dashboard action into
    the column values below.
    """

    def __init__(self, table: SheetTable) -> None:
        self._table = table

    def record(self, values: Mapping[str, Any]) -> None:
        self._table.append(values)

    def record_many(self, rows: list[Mapping[str, Any]]) -> None:
        self._table.append_many(rows)

    def for_run(self, run_id: str) -> list[Row]:
        return self._table.find(run_id=run_id)

    def all(self) -> list[Row]:
        return self._table.rows()


class SystemRunsRepository:
    """The ``System_Runs`` tab — one row per processing run (CLAUDE.md §12/§13).

    Mostly append-only, like ``Audit_Log`` — with one deliberate exception.
    ``undo_available`` is not a historical fact fixed at write time the way
    every other column is; it's a live status ("can Phase 12 still reverse
    this run?") that necessarily changes once someone does. Rather than
    falsify history by rewriting ``Audit_Log``, or invent a way to infer
    "already undone" from an ever-growing scan of appended rows, this one
    column gets a narrow, explicit :meth:`mark_undone` update — the same
    generic :meth:`SheetTable.update` every other repository already uses
    for its own current-status fields (e.g. ``Settings``).
    """

    def __init__(self, table: SheetTable) -> None:
        self._table = table

    def record(
        self,
        *,
        run_id: str,
        mode: str,
        started_at: str,
        completed_at: str,
        emails_processed: int,
        emails_changed: int = 0,
        errors: int = 0,
        undo_available: bool = False,
    ) -> None:
        self._table.append(
            {
                "run_id": run_id,
                "mode": mode,
                "started_at": started_at,
                "completed_at": completed_at,
                "emails_processed": str(emails_processed),
                "emails_changed": str(emails_changed),
                "errors": str(errors),
                "undo_available": "true" if undo_available else "false",
            }
        )

    def for_run(self, run_id: str) -> Row | None:
        return self._table.first(run_id=run_id)

    def all(self) -> list[Row]:
        return self._table.rows()

    def latest_undoable(self) -> Row | None:
        """The most recent run Phase 12's Undo Last Run could still reverse.

        Rows are appended in chronological order, so the last matching row
        in the sheet is the most recent run.
        """
        for row in reversed(self.all()):
            if row.get("undo_available", "").strip().lower() in _TRUE_VALUES:
                return row
        return None

    def mark_undone(self, run_id: str) -> bool:
        """Flip ``undo_available`` off after a run has been reversed.

        Returns ``False`` if the run doesn't exist — callers treat that as
        "nothing to mark," not an error.
        """
        row = self._table.first(run_id=run_id)
        if row is None:
            return False
        self._table.update(row, {"undo_available": "false"})
        return True


class DigestRepository:
    """The ``Digest_Log`` tab — one row per calendar date's generated digest
    (CLAUDE.md §13/§14).

    Keyed on ``digest_date`` so re-generating the same day's digest (a manual
    ``/digest/scan`` call, or the background scheduler recovering after a
    restart) updates the one row instead of piling up duplicates — the same
    idempotency guarantee every other keyed writer in this app gives
    (Deadlines, Subscriptions, Trips).

    This is a summary record, not a snapshot of the digest's actual content:
    like ``System_Runs``, it says a run happened and what it found, not the
    full per-message detail — ``Audit_Log`` already owns that history, and
    duplicating it here would just be a second source of truth for the same
    facts. The dashboard's own digest page always recomputes fresh from
    current mail, matching how the rest of the Command Center works.
    """

    KEY = ("digest_date",)

    def __init__(self, table: SheetTable) -> None:
        self._table = table
        self._keyed = _KeyedTable(table, self.KEY)

    def record(
        self,
        *,
        digest_date: str,
        generated_at: str,
        timezone: str,
        account: str,
        counts: Mapping[str, int],
        total: int,
    ) -> str:
        return self._keyed.upsert(
            {
                "digest_id": f"digest-{digest_date}",
                "digest_date": digest_date,
                "generated_at": generated_at,
                "timezone": timezone,
                "account": account,
                "p1_count": str(counts.get("p1", 0)),
                "p2_count": str(counts.get("p2", 0)),
                "action_count": str(counts.get("action", 0)),
                "overdue_count": str(counts.get("overdue", 0)),
                "waiting_count": str(counts.get("waiting", 0)),
                "due_soon_count": str(counts.get("due_soon", 0)),
                "review_count": str(counts.get("review", 0)),
                "total_count": str(total),
            }
        )

    def for_date(self, digest_date: str) -> Row | None:
        return self._table.first(digest_date=digest_date)

    def latest(self) -> Row | None:
        rows = self._table.rows()
        if not rows:
            return None
        return max(rows, key=lambda r: r.get("digest_date", ""))

    def all(self) -> list[Row]:
        return self._table.rows()


class _KeyedTable:
    """A tab whose rows are keyed for idempotent upserts.

    Phase 6 writes intelligence rows on every run. Without a key, re-running a
    scan would pile up duplicate Deadlines and Subscriptions. Keying on stable
    columns (a message + date, a service, a trip id) makes a repeat run *update*
    the existing row instead — which is what CLAUDE.md §13 means by idempotent.
    """

    def __init__(self, table: SheetTable, key_columns: tuple[str, ...]) -> None:
        self._table = table
        self._key = key_columns

    def upsert(self, values: Mapping[str, Any]) -> str:
        key_filter = {col: str(values.get(col, "") or "") for col in self._key}
        existing = self._table.first(**key_filter)
        if existing is None:
            self._table.append(values)
            return "inserted"
        self._table.update(existing, values)
        return "updated"

    def rows(self) -> list[Row]:
        return self._table.rows()


class DeadlinesRepository:
    """The ``Deadlines`` tab. One row per (message, date)."""

    KEY = ("message_id", "normalized_date")

    def __init__(self, table: SheetTable) -> None:
        self._keyed = _KeyedTable(table, self.KEY)

    def upsert(self, values: Mapping[str, Any]) -> str:
        return self._keyed.upsert(values)

    def all(self) -> list[Row]:
        return self._keyed.rows()


class SubscriptionsRepository:
    """The ``Subscriptions`` tab. One row per (service, sender_domain)."""

    KEY = ("service", "sender_domain")

    def __init__(self, table: SheetTable) -> None:
        self._keyed = _KeyedTable(table, self.KEY)

    def upsert(self, values: Mapping[str, Any]) -> str:
        return self._keyed.upsert(values)

    def all(self) -> list[Row]:
        return self._keyed.rows()


class TripsRepository:
    """The ``Trips`` tab. One row per trip id."""

    KEY = ("trip_id",)

    def __init__(self, table: SheetTable) -> None:
        self._keyed = _KeyedTable(table, self.KEY)

    def upsert(self, values: Mapping[str, Any]) -> str:
        return self._keyed.upsert(values)

    def all(self) -> list[Row]:
        return self._keyed.rows()


# --------------------------------------------------------------------
# Workbook handle
# --------------------------------------------------------------------


class ControlWorkbook:
    """Entry point for all workbook access."""

    def __init__(self, spreadsheet_id: str, sheets: Resource) -> None:
        self.spreadsheet_id = spreadsheet_id
        self._sheets = sheets
        self._tables: dict[str, SheetTable] = {}

    @classmethod
    def connect(cls, spreadsheet_id: str | None = None) -> "ControlWorkbook":
        """Build a handle from the stored OAuth token.

        Ensures the workbook exists and matches the schema first.
        """
        from app.sheets.workbook import ensure_workbook

        sheets = get_sheets_service()
        info = ensure_workbook(sheets=sheets, spreadsheet_id=spreadsheet_id)
        return cls(spreadsheet_id=info.spreadsheet_id, sheets=sheets)

    def table(self, tab: Tab | str) -> SheetTable:
        resolved = tab_by_name(tab) if isinstance(tab, str) else tab
        if resolved.name not in self._tables:
            self._tables[resolved.name] = SheetTable(
                tab=resolved, spreadsheet_id=self.spreadsheet_id, sheets=self._sheets
            )
        return self._tables[resolved.name]

    def invalidate_all(self) -> None:
        for table in self._tables.values():
            table.invalidate()

    @property
    def settings(self) -> SettingsRepository:
        return SettingsRepository(self.table(SETTINGS_TAB))

    @property
    def rules(self) -> RulesRepository:
        return RulesRepository(
            sender_table=self.table(SENDER_RULES_TAB),
            domain_table=self.table(DOMAIN_RULES_TAB),
            suggestions_table=self.table(LEARNED_RULE_SUGGESTIONS_TAB),
        )

    @property
    def vips(self) -> VIPRepository:
        return VIPRepository(self.table(VIPS_TAB))

    @property
    def deadlines(self) -> DeadlinesRepository:
        return DeadlinesRepository(self.table(DEADLINES_TAB))

    @property
    def subscriptions(self) -> SubscriptionsRepository:
        return SubscriptionsRepository(self.table(SUBSCRIPTIONS_TAB))

    @property
    def trips(self) -> TripsRepository:
        return TripsRepository(self.table(TRIPS_TAB))

    @property
    def review_feedback(self) -> ReviewFeedbackRepository:
        return ReviewFeedbackRepository(self.table(REVIEW_FEEDBACK_TAB))

    @property
    def audit_log(self) -> AuditRepository:
        return AuditRepository(self.table(AUDIT_LOG_TAB))

    @property
    def system_runs(self) -> SystemRunsRepository:
        return SystemRunsRepository(self.table(SYSTEM_RUNS_TAB))

    @property
    def digest_log(self) -> DigestRepository:
        return DigestRepository(self.table(DIGEST_LOG_TAB))


__all__ = (
    "CACHE_TTL_SECONDS",
    "AuditRepository",
    "ControlWorkbook",
    "DeadlinesRepository",
    "DigestRepository",
    "DomainRule",
    "ReviewFeedbackRepository",
    "Row",
    "RulesRepository",
    "SenderRule",
    "SettingsRepository",
    "SheetTable",
    "SubscriptionsRepository",
    "SystemRunsRepository",
    "TripsRepository",
    "VIP",
    "VIPRepository",
)
