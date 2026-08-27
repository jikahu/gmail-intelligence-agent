"""Parsing config/rules.toml (CLAUDE.md §11), including vendor_rules."""

from __future__ import annotations

from pathlib import Path

from app.rules.store import VendorRuleRow, load_rules


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_vendor_rules(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[vendor_rules]]
        match = "subject_contains"
        value = "equity"
        label = "Equity"

        [[vendor_rules]]
        match = "sender_contains"
        value = "arvocap"
        label = "Arvocap"
        """,
    )

    rules = load_rules(path)

    assert rules.vendor_rules == (
        VendorRuleRow(match="subject_contains", value="equity", label="Equity"),
        VendorRuleRow(match="sender_contains", value="arvocap", label="Arvocap"),
    )


def test_missing_vendor_rules_section_is_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "")

    rules = load_rules(path)

    assert rules.vendor_rules == ()


def test_vendor_rule_row_without_label_is_dropped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[vendor_rules]]
        match = "subject_contains"
        value = "equity"
        """,
    )

    rules = load_rules(path)

    assert rules.vendor_rules == ()
