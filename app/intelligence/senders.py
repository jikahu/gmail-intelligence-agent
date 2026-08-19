"""Deriving a human brand name from a sender (shared helper).

Subscriptions and orders both want to show "Netflix" or "Amazon" rather than
``no-reply@netflix.com``. The logic is small but shared, so it lives here.
"""

from __future__ import annotations

import re

from app.classification.message import EmailMessage

_NOREPLY = re.compile(
    r"no.?reply|do.?not.?reply|notifications?|team|billing|info|support|"
    r"orders?|shipping|account|service|mailer|newsletter",
    re.IGNORECASE,
)


def brand_from_domain(domain: str) -> str:
    parts = [p for p in (domain or "").split(".") if p]
    if len(parts) >= 2:
        return parts[-2].replace("-", " ").title()
    return domain or ""


def brand_name(message: EmailMessage, fallback: str = "Unknown") -> str:
    """A display name for the sender's organisation."""
    name = (message.sender_name or "").strip()
    if name and "@" not in name and not _NOREPLY.search(name):
        return name
    domain = message.sender_registrable_domain or message.sender_domain
    return brand_from_domain(domain) or name or fallback


__all__ = ("brand_from_domain", "brand_name")
