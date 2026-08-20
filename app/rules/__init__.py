"""Local, user-edited rules (VIPs, sender rules, domain rules).

Replaces the old Google Sheets control workbook. For a single-user personal
tool there's no need for a spreadsheet, an approval-workflow UI, or a Sheets
API dependency -- a small file the user (or Claude Code, on their behalf)
edits directly is just as auditable and far simpler (CLAUDE.md §21: simple,
understandable architecture over clever architecture).
"""

from __future__ import annotations

from app.rules.store import RulesFile, load_rules

__all__ = ("RulesFile", "load_rules")
