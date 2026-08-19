"""Control workbook schema (CLAUDE.md §12).

Every tab of the workbook is declared here as an immutable dataclass. The
workbook manager (`app/sheets/workbook.py`) reads this file to:

1. Create missing tabs on first boot.
2. Detect columns that later phases add and append them to the end of an
   existing tab (additive drift — we never rename or delete columns).
3. Refuse to boot if two tabs share a name or two columns in the same tab do.

Later phases add tabs by appending to :data:`WORKBOOK_TABS`. Do not reorder
existing entries; append only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class Column:
    """A single column definition."""

    name: str
    #: Human-readable purpose. Never rendered into the header row.
    description: str = ""


@dataclass(frozen=True)
class Tab:
    """A single tab definition."""

    name: str
    columns: tuple[Column, ...]
    #: What this tab is for, in one line. Rendered in the plain-English doc.
    purpose: str = ""

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


# --------------------------------------------------------------------
# Tab definitions (order matches CLAUDE.md §12).
# --------------------------------------------------------------------

SETTINGS_TAB = Tab(
    name="Settings",
    purpose="Editable app-wide switches (AI provider, dry-run flag, feature flags, digest hour, etc.).",
    columns=(
        Column("key", "Stable identifier, e.g. `dry_run` or `ai_provider`."),
        Column("value", "Current value as a string."),
        Column("description", "What this setting controls."),
        Column("updated_at", "ISO 8601 timestamp of the last change."),
    ),
)

VIPS_TAB = Tab(
    name="VIPs",
    purpose="Approved VIP senders. VIP email stays in inbox and is protected from routine Review.",
    columns=(
        Column("email"),
        Column("name"),
        Column("status", "`approved` or `pending` (from a learning suggestion)."),
        Column("approved_at"),
        Column("notes"),
    ),
)

SENDER_RULES_TAB = Tab(
    name="Sender_Rules",
    purpose="Per-sender rules that override deterministic classification.",
    columns=(
        Column("sender"),
        Column("rule_type", "`whitelist`, `blacklist`, or `classify_as`."),
        Column("action", "For `classify_as`, the label to force (e.g. `AI/Personal`)."),
        Column("status", "`active`, `paused`, or `pending`."),
        Column("source", "`manual` or `learned`."),
        Column("approved_at"),
        Column("notes"),
    ),
)

DOMAIN_RULES_TAB = Tab(
    name="Domain_Rules",
    purpose="Per-domain rules. Applies to the actual sender domain, never display name.",
    columns=(
        Column("domain"),
        Column("rule_type"),
        Column("action"),
        Column("status"),
        Column("source"),
        Column("approved_at"),
        Column("notes"),
    ),
)

LEARNED_RULE_SUGGESTIONS_TAB = Tab(
    name="Learned_Rule_Suggestions",
    purpose="Rule ideas surfaced from user corrections — require explicit approval, never auto-applied.",
    columns=(
        Column("suggestion_id"),
        Column("target", "sender, domain, or pattern string."),
        Column("suggested_rule"),
        Column("evidence", "Short summary of what led to the suggestion."),
        Column("confidence", "0.0 to 1.0."),
        Column("status", "`pending`, `approved`, `rejected`."),
        Column("created_at"),
        Column("approved_at"),
    ),
)

REVIEW_FEEDBACK_TAB = Tab(
    name="Review_Feedback",
    purpose="Dashboard actions on Review-queue rows: Keep, Correct, Restore, Make Rule, Suggest VIP.",
    columns=(
        Column("gmail_message_id"),
        Column("thread_id"),
        Column("original_classification"),
        Column("original_reason"),
        Column("user_decision"),
        Column("resulting_rule_suggestion", "suggestion_id if this feedback produced one."),
        Column("timestamp"),
    ),
)

AUDIT_LOG_TAB = Tab(
    name="Audit_Log",
    purpose="Every automated Gmail action + every dashboard action. Powers Undo Last Run.",
    columns=(
        Column("event_id"),
        Column("run_id"),
        Column("timestamp"),
        Column("gmail_message_id"),
        Column("thread_id"),
        Column("subject_safe_ref", "Short truncated subject — never full body."),
        Column("classification"),
        Column("priority"),
        Column("confidence"),
        Column("rules_triggered"),
        Column("ai_reason_summary", "One-line rationale; NEVER hidden chain-of-thought."),
        Column("labels_before"),
        Column("labels_after"),
        Column("inbox_before"),
        Column("inbox_after"),
        Column("action_taken"),
        Column("actor", "`agent`, `user`, `system`."),
        Column("reversible"),
        Column("undo_status"),
    ),
)

DEADLINES_TAB = Tab(
    name="Deadlines",
    purpose="Extracted deadlines (payment due, respond-by, interview, renewal, etc.).",
    columns=(
        Column("message_id"),
        Column("thread_id"),
        Column("deadline"),
        Column("original_text"),
        Column("normalized_date", "ISO 8601 date."),
        Column("status", "`upcoming`, `due_soon`, `overdue`, `resolved`."),
        Column("confidence"),
        Column("category"),
    ),
)

SUBSCRIPTIONS_TAB = Tab(
    name="Subscriptions",
    purpose="Recurring charges + memberships. Agent may suggest cancellation review; never auto-cancels.",
    columns=(
        Column("service"),
        Column("sender_domain"),
        Column("amount"),
        Column("currency"),
        Column("billing_frequency"),
        Column("renewal_date"),
        Column("last_seen"),
        Column("review_status"),
    ),
)

TRIPS_TAB = Tab(
    name="Trips",
    purpose="Grouped travel context (flights, hotels, cars, itinerary changes).",
    columns=(
        Column("trip_id"),
        Column("destination"),
        Column("start_date"),
        Column("end_date"),
        Column("related_threads", "Comma-separated Gmail thread IDs."),
        Column("status"),
    ),
)

SYSTEM_RUNS_TAB = Tab(
    name="System_Runs",
    purpose="One row per processing run — enables Undo Last Run and cost accounting.",
    columns=(
        Column("run_id"),
        Column("mode", "`dry_run`, `live`, `historical`, `real_time`."),
        Column("started_at"),
        Column("completed_at"),
        Column("emails_processed"),
        Column("emails_changed"),
        Column("errors"),
        Column("undo_available"),
    ),
)

DIGEST_LOG_TAB = Tab(
    name="Digest_Log",
    purpose=(
        "One row per generated daily digest (CLAUDE.md §13/§14) — a record "
        "that a digest ran and what it found. Not the message-level detail; "
        "that's Audit_Log's job, and the dashboard's own digest page always "
        "recomputes fresh from current mail rather than reading this back."
    ),
    columns=(
        Column("digest_id"),
        Column("digest_date", "The calendar date this digest covers, in `digest_timezone`. ISO 8601 date."),
        Column("generated_at", "ISO 8601 UTC timestamp of when this digest was actually built."),
        Column("timezone", "TZ database name digest_hour/digest_date were evaluated in."),
        Column("account"),
        Column("p1_count"),
        Column("p2_count"),
        Column("action_count"),
        Column("overdue_count"),
        Column("waiting_count"),
        Column("due_soon_count"),
        Column("review_count"),
        Column("total_count"),
    ),
)


WORKBOOK_TABS: tuple[Tab, ...] = (
    SETTINGS_TAB,
    VIPS_TAB,
    SENDER_RULES_TAB,
    DOMAIN_RULES_TAB,
    LEARNED_RULE_SUGGESTIONS_TAB,
    REVIEW_FEEDBACK_TAB,
    AUDIT_LOG_TAB,
    DEADLINES_TAB,
    SUBSCRIPTIONS_TAB,
    TRIPS_TAB,
    SYSTEM_RUNS_TAB,
    DIGEST_LOG_TAB,
)


# --------------------------------------------------------------------
# Defaults seeded into Settings on first workbook creation.
# --------------------------------------------------------------------

DEFAULT_SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("dry_run", "true", "When true, the app performs ZERO Gmail modifications."),
    ("gmail_processing_enabled", "false", "Master switch for any Gmail processing."),
    ("ai_provider", "anthropic", "`anthropic` or `openai`."),
    ("anthropic_model", "claude-opus-5", "Model identifier for the Anthropic provider."),
    ("openai_model", "gpt-4o-mini", "Model identifier for the OpenAI provider."),
    ("ai_effort", "low", "How hard the AI works: low, medium, high, xhigh, max."),
    ("digest_timezone", "America/New_York", "TZ database name for the daily digest."),
    ("digest_hour", "0", "Digest send hour in `digest_timezone`."),
    ("review_confidence_threshold", "0.7", "AI classifications below this go to AI/Review."),
)


def validate_schema() -> None:
    """Sanity-check the schema at import time. Raises ``ValueError`` on drift."""
    seen_tabs: set[str] = set()
    for tab in WORKBOOK_TABS:
        if tab.name in seen_tabs:
            raise ValueError(f"Duplicate tab name in WORKBOOK_TABS: {tab.name!r}")
        seen_tabs.add(tab.name)
        seen_columns: set[str] = set()
        for col in tab.columns:
            if col.name in seen_columns:
                raise ValueError(
                    f"Duplicate column {col.name!r} in tab {tab.name!r}"
                )
            seen_columns.add(col.name)


def tab_by_name(name: str) -> Tab:
    for tab in WORKBOOK_TABS:
        if tab.name == name:
            return tab
    raise KeyError(f"Unknown tab: {name!r}")


def all_tab_names() -> Sequence[str]:
    return [t.name for t in WORKBOOK_TABS]


# Fail fast on import if someone edits the schema incorrectly.
validate_schema()


__all__ = (
    "Column",
    "Tab",
    "WORKBOOK_TABS",
    "DEFAULT_SETTINGS",
    "SETTINGS_TAB",
    "VIPS_TAB",
    "SENDER_RULES_TAB",
    "DOMAIN_RULES_TAB",
    "LEARNED_RULE_SUGGESTIONS_TAB",
    "REVIEW_FEEDBACK_TAB",
    "AUDIT_LOG_TAB",
    "DEADLINES_TAB",
    "SUBSCRIPTIONS_TAB",
    "TRIPS_TAB",
    "SYSTEM_RUNS_TAB",
    "DIGEST_LOG_TAB",
    "validate_schema",
    "tab_by_name",
    "all_tab_names",
)
