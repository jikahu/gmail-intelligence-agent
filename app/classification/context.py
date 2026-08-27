"""Everything the engine needs to know that isn't in the email itself.

The engine is a pure function of ``(message, context)``. It never reaches out
to Sheets, Gmail, or the network — the caller assembles a
:class:`ClassificationContext` first. That's what makes the rules testable
with a dozen lines of fixture and no mocking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.classification.patterns import PUBLIC_EMAIL_PROVIDERS
from app.logging_config import get_logger

log = get_logger("app.classification.context")

#: Rule types understood by the engine (matches the workbook's `rule_type`).
RULE_WHITELIST = "whitelist"
RULE_BLACKLIST = "blacklist"
RULE_CLASSIFY_AS = "classify_as"

VALID_RULE_TYPES: frozenset[str] = frozenset(
    {RULE_WHITELIST, RULE_BLACKLIST, RULE_CLASSIFY_AS}
)

#: How a vendor rule's ``value`` is matched against the message.
VENDOR_MATCH_SUBJECT_CONTAINS = "subject_contains"
#: Checks the sender's domain *and* display name -- either counts as a match.
VENDOR_MATCH_SENDER_CONTAINS = "sender_contains"

VALID_VENDOR_MATCH_TYPES: frozenset[str] = frozenset(
    {VENDOR_MATCH_SUBJECT_CONTAINS, VENDOR_MATCH_SENDER_CONTAINS}
)


@dataclass(frozen=True)
class Rule:
    """A manual sender or domain rule, normalized."""

    target: str
    rule_type: str
    action: str = ""
    source: str = "manual"
    scope: str = "sender"  # "sender" or "domain"

    @property
    def is_whitelist(self) -> bool:
        return self.rule_type == RULE_WHITELIST

    @property
    def is_blacklist(self) -> bool:
        return self.rule_type == RULE_BLACKLIST

    def describe(self) -> str:
        detail = f" → {self.action}" if self.action else ""
        return f"{self.scope} rule {self.rule_type} for {self.target}{detail}"


@dataclass(frozen=True)
class VendorRule:
    """A config-defined swap (CLAUDE.md §11): mail matching ``value`` gets
    ``label`` -- an existing Gmail folder the user already made, never a
    label this app creates itself -- instead of whatever the deterministic
    engine would otherwise categorize it as."""

    match: str
    value: str
    label: str

    def describe(self) -> str:
        return f"vendor rule {self.match}={self.value!r} → {self.label}"


@dataclass
class ClassificationContext:
    """Who the user trusts, and what they've told us explicitly."""

    user_email: str = ""
    sender_rules: dict[str, Rule] = field(default_factory=dict)
    domain_rules: dict[str, Rule] = field(default_factory=dict)
    vendor_rules: tuple[VendorRule, ...] = ()
    vip_emails: set[str] = field(default_factory=set)
    #: Google Contacts + Other Contacts.
    known_contacts: set[str] = field(default_factory=set)
    #: Addresses the user has actually emailed or replied to.
    prior_correspondents: set[str] = field(default_factory=set)

    # -------- Lookups --------

    def sender_rule_for(self, email_address: str) -> Rule | None:
        return self.sender_rules.get((email_address or "").strip().lower())

    def domain_rule_for(self, domain: str) -> Rule | None:
        return self.domain_rules.get((domain or "").strip().lower().lstrip("@"))

    def is_vip(self, email_address: str) -> bool:
        return (email_address or "").strip().lower() in self.vip_emails

    def is_known_contact(self, email_address: str) -> bool:
        return (email_address or "").strip().lower() in self.known_contacts

    def is_prior_correspondent(self, email_address: str) -> bool:
        return (email_address or "").strip().lower() in self.prior_correspondents

    def is_self(self, email_address: str) -> bool:
        normalized = (email_address or "").strip().lower()
        return bool(normalized) and normalized == self.user_email.strip().lower()


def build_rule(
    target: str,
    rule_type: str,
    action: str = "",
    source: str = "manual",
    scope: str = "sender",
) -> Rule | None:
    """Normalize one workbook row into a :class:`Rule`, or ``None`` if unusable.

    Domain rules on public mailbox providers are **refused**. Approving one
    person at ``gmail.com`` must never trust every Gmail address on earth
    (CLAUDE.md §8) — so this returns ``None`` and logs, rather than quietly
    creating a rule with enormous blast radius.
    """
    normalized_target = (target or "").strip().lower().lstrip("@")
    normalized_type = (rule_type or "").strip().lower()

    if not normalized_target:
        return None
    if normalized_type not in VALID_RULE_TYPES:
        log.warning(
            "rule_ignored_unknown_type",
            extra={"rule_target": normalized_target, "rule_type": normalized_type},
        )
        return None
    if scope == "domain" and normalized_target in PUBLIC_EMAIL_PROVIDERS:
        log.warning(
            "domain_rule_refused_public_provider",
            extra={"rule_target": normalized_target},
        )
        return None

    return Rule(
        target=normalized_target,
        rule_type=normalized_type,
        action=(action or "").strip(),
        source=(source or "manual").strip().lower(),
        scope=scope,
    )


def build_vendor_rule(match: str, value: str, label: str) -> VendorRule | None:
    """Normalize one ``[[vendor_rules]]`` row, or ``None`` if unusable."""
    normalized_match = (match or "").strip().lower()
    normalized_value = (value or "").strip().lower()
    normalized_label = (label or "").strip()

    if normalized_match not in VALID_VENDOR_MATCH_TYPES:
        log.warning(
            "vendor_rule_ignored_unknown_match_type",
            extra={"vendor_rule_match": normalized_match},
        )
        return None
    if not normalized_value or not normalized_label:
        return None

    return VendorRule(match=normalized_match, value=normalized_value, label=normalized_label)


def context_from_rules(
    rules_file,
    user_email: str = "",
    known_contacts: set[str] | None = None,
    prior_correspondents: set[str] | None = None,
) -> ClassificationContext:
    """Assemble a context from the local rules file.

    ``rules_file`` is an :class:`app.rules.store.RulesFile`. Typed loosely so
    this module doesn't import the rules-storage layer — the engine stays
    independent of where the rules are stored.
    """
    sender_rules: dict[str, Rule] = {}
    for row in rules_file.sender_rules:
        rule = build_rule(
            target=row.sender,
            rule_type=row.rule_type,
            action=row.action,
            source=row.source,
            scope="sender",
        )
        if rule is not None:
            sender_rules[rule.target] = rule

    domain_rules: dict[str, Rule] = {}
    for row in rules_file.domain_rules:
        rule = build_rule(
            target=row.domain,
            rule_type=row.rule_type,
            action=row.action,
            source=row.source,
            scope="domain",
        )
        if rule is not None:
            domain_rules[rule.target] = rule

    vendor_rules = tuple(
        vendor_rule
        for row in rules_file.vendor_rules
        if (vendor_rule := build_vendor_rule(row.match, row.value, row.label)) is not None
    )

    return ClassificationContext(
        user_email=(user_email or "").strip().lower(),
        sender_rules=sender_rules,
        domain_rules=domain_rules,
        vendor_rules=vendor_rules,
        vip_emails=set(rules_file.vip_emails),
        known_contacts={c.lower() for c in (known_contacts or set())},
        prior_correspondents={c.lower() for c in (prior_correspondents or set())},
    )


__all__ = (
    "ClassificationContext",
    "RULE_BLACKLIST",
    "RULE_CLASSIFY_AS",
    "RULE_WHITELIST",
    "VALID_VENDOR_MATCH_TYPES",
    "VENDOR_MATCH_SENDER_CONTAINS",
    "VENDOR_MATCH_SUBJECT_CONTAINS",
    "Rule",
    "VALID_RULE_TYPES",
    "VendorRule",
    "build_rule",
    "build_vendor_rule",
    "context_from_rules",
)
