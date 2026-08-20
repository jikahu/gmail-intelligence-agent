"""Reads ``config/rules.toml`` -- VIPs, sender rules, and domain rules.

This is the single file that replaces the Sheets workbook's ``VIPs``,
``Sender_Rules``, and ``Domain_Rules`` tabs. It's read-only from the app's
side: there is no dashboard, no approval-workflow UI, and no code path that
writes to it. The user edits it by hand (or asks Claude Code to), commits
it like any other config, and it ships with the app on every deploy --
which also means it survives a Render redeploy for free, unlike the old
Sheets workbook id that had to be pinned in an env var.

The file is optional. A missing file behaves exactly like an empty one:
no VIPs, no sender/domain rules, protection rules relying on those are
simply not there. That's the safe direction -- a thinner config makes
*less* eligible for special treatment, never more.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from app.logging_config import get_logger

log = get_logger("app.rules.store")

#: Project-root config file. Not secret -- fine to commit -- so this lives
#: next to render.yaml and pyproject.toml, not in the gitignored oauth_tokens/
#: directory used for actual credentials.
RULES_FILE = Path("config/rules.toml")


@dataclass(frozen=True)
class SenderRuleRow:
    sender: str
    rule_type: str
    action: str = ""
    source: str = "manual"


@dataclass(frozen=True)
class DomainRuleRow:
    domain: str
    rule_type: str
    action: str = ""
    source: str = "manual"


@dataclass(frozen=True)
class RulesFile:
    """The parsed contents of ``config/rules.toml``."""

    vip_emails: frozenset[str] = field(default_factory=frozenset)
    sender_rules: tuple[SenderRuleRow, ...] = ()
    domain_rules: tuple[DomainRuleRow, ...] = ()


def _load_raw(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        log.warning("rules_file_unparsable", extra={"path": str(path), "error": str(exc)})
        return {}


def load_rules(path: Path | None = None) -> RulesFile:
    """Parse the rules file. Never raises -- a bad or missing file just
    means an empty ruleset, the same "degrade, don't fail" contract the old
    Sheets-backed context builder had for a missing workbook.

    ``path`` defaults to the *current* value of :data:`RULES_FILE`, looked up
    at call time rather than bound as a function default -- tests monkeypatch
    that module attribute to isolate themselves from the real checked-in
    file, and a bound default would silently ignore that.
    """
    raw = _load_raw(path if path is not None else RULES_FILE)

    vip_emails = frozenset(
        str(row.get("email", "")).strip().lower()
        for row in raw.get("vips", []) or []
        if str(row.get("email", "")).strip()
    )

    sender_rules = tuple(
        SenderRuleRow(
            sender=str(row.get("sender", "")).strip(),
            rule_type=str(row.get("rule_type", "")).strip(),
            action=str(row.get("action", "")).strip(),
            source=str(row.get("source", "manual")).strip(),
        )
        for row in raw.get("sender_rules", []) or []
        if str(row.get("sender", "")).strip()
    )

    domain_rules = tuple(
        DomainRuleRow(
            domain=str(row.get("domain", "")).strip(),
            rule_type=str(row.get("rule_type", "")).strip(),
            action=str(row.get("action", "")).strip(),
            source=str(row.get("source", "manual")).strip(),
        )
        for row in raw.get("domain_rules", []) or []
        if str(row.get("domain", "")).strip()
    )

    return RulesFile(
        vip_emails=vip_emails, sender_rules=sender_rules, domain_rules=domain_rules
    )


__all__ = ("RULES_FILE", "DomainRuleRow", "RulesFile", "SenderRuleRow", "load_rules")
