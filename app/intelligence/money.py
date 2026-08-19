"""Currency + amount extraction, and safe account references (CLAUDE.md §7).

Two safety rules shape this module:

* **We only ever keep the last four digits of an account or card number.** The
  extractor has no code path that returns a full number — even when a message
  contains one in the clear, :func:`extract_account_refs` reduces it to four
  digits. CLAUDE.md §7 and §16: store the minimum, never the whole thing.
* **A bare number is never money.** An amount is only recognised when a
  currency marker (a symbol like ``$`` or a code like ``KES``) sits next to it.
  That keeps order numbers, years and phone numbers from being read as sums.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Symbol → ISO-ish currency code. ``$`` is read as USD; a message from a
#: CAD/AUD sender would need context we don't have here, so we keep the common
#: reading and let a human correct it rather than guessing per-message.
_SYMBOL_CURRENCY: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₦": "NGN",
}

#: Text currency codes / words we recognise. KES/KSh matters for this user
#: (Kenya), alongside the usual majors.
_CODE_CURRENCY: dict[str, str] = {
    "usd": "USD", "dollars": "USD", "dollar": "USD",
    "eur": "EUR", "euros": "EUR", "euro": "EUR",
    "gbp": "GBP", "pounds": "GBP", "pound": "GBP", "gbp£": "GBP",
    "kes": "KES", "ksh": "KES", "kshs": "KES", "shillings": "KES", "shilling": "KES",
    "jpy": "JPY", "yen": "JPY",
    "cad": "CAD", "aud": "AUD", "inr": "INR", "rupees": "INR",
    "zar": "ZAR", "rand": "ZAR", "ngn": "NGN", "naira": "NGN",
    "chf": "CHF", "cny": "CNY", "rmb": "CNY",
}

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_SYMBOL_ALT = "|".join(re.escape(s) for s in ("$", "€", "£", "¥", "₹", "₦"))
_CODE_ALT = "|".join(sorted(_CODE_CURRENCY, key=len, reverse=True))

# $1,234.56  /  € 99  /  KES 5,000  /  Ksh5000
_SYMBOL_AMOUNT_RE = re.compile(rf"({_SYMBOL_ALT})\s?({_NUM})")
_CODE_AMOUNT_RE = re.compile(rf"\b({_CODE_ALT})\s?\.?\s?({_NUM})\b", re.IGNORECASE)
# 1,234.56 USD  /  99 euros  /  5000 shillings
_AMOUNT_CODE_RE = re.compile(rf"\b({_NUM})\s?({_CODE_ALT})\b", re.IGNORECASE)

#: Wording that tells us *which* amount matters, most decisive first.
_CONTEXT_KEYWORDS: tuple[tuple[str, int], ...] = (
    ("amount due", 5), ("total due", 5), ("balance due", 5), ("payment due", 5),
    ("minimum payment", 5), ("grand total", 4), ("total amount", 4),
    ("total", 3), ("balance", 3), ("amount", 2), ("due", 2),
    ("refund", 2), ("charged", 2), ("payment", 1), ("price", 1),
)


@dataclass(frozen=True)
class MoneyAmount:
    """One monetary amount, with the wording it came from."""

    amount: float
    currency: str
    original_text: str
    start: int = 0
    end: int = 0

    def formatted(self) -> str:
        return f"{self.currency} {self.amount:,.2f}"


@dataclass(frozen=True)
class AccountRef:
    """A *safe* reference to an account/card — the last four digits only.

    There is deliberately no field for the full number. If you find yourself
    wanting one, re-read CLAUDE.md §7.
    """

    last4: str
    original_text: str


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def extract_money(text: str) -> list[MoneyAmount]:
    """Return every currency amount in ``text``, in the order they appear."""
    if not text:
        return []

    found: dict[tuple[int, int], MoneyAmount] = {}

    for m in _SYMBOL_AMOUNT_RE.finditer(text):
        amount = _to_float(m.group(2))
        if amount is not None:
            found[(m.start(), m.end())] = MoneyAmount(
                amount, _SYMBOL_CURRENCY.get(m.group(1), "USD"),
                m.group(0).strip(), m.start(), m.end(),
            )

    for m in _CODE_AMOUNT_RE.finditer(text):
        amount = _to_float(m.group(2))
        if amount is not None:
            found[(m.start(), m.end())] = MoneyAmount(
                amount, _CODE_CURRENCY[m.group(1).lower()],
                m.group(0).strip(), m.start(), m.end(),
            )

    for m in _AMOUNT_CODE_RE.finditer(text):
        span = (m.start(), m.end())
        if any(s < span[1] and e > span[0] for s, e in found):
            continue  # already captured by a symbol/code-prefixed match
        amount = _to_float(m.group(1))
        if amount is not None:
            found[span] = MoneyAmount(
                amount, _CODE_CURRENCY[m.group(2).lower()],
                m.group(0).strip(), m.start(), m.end(),
            )

    return [found[key] for key in sorted(found)]


def primary_money(text: str, amounts: list[MoneyAmount] | None = None) -> MoneyAmount | None:
    """Pick the amount that best answers "how much does this email want?".

    Scored by nearby wording ("amount due", "total", "balance"…) and, as a
    tie-breaker, by size — a statement's headline figure is usually the largest.
    """
    amounts = amounts if amounts is not None else extract_money(text)
    if not amounts:
        return None
    lower = text.lower()

    def score(amount: MoneyAmount) -> tuple[int, float]:
        window = lower[max(0, amount.start - 30): amount.start]
        best = 0
        for keyword, weight in _CONTEXT_KEYWORDS:
            if keyword in window:
                best = max(best, weight)
        return best, amount.amount

    return max(amounts, key=score)


# --------------------------------------------------------------------
# Account / card references — masking is the whole point.
# --------------------------------------------------------------------

# "ending in 1234" / "ending 1234" / "acct ...1234" / "card no. 1234".
# The trailing lookahead means we only grab a *final* four-digit group, so the
# leading BIN of a full card number ("4111 1111…") is left for _LONG_NUMBER_RE
# to mask down to its true last four.
_ENDING_RE = re.compile(
    r"(?:ending(?:\s+in)?|acct|account|card|a/c|no\.?)\D{0,12}?(\d{4})\b(?![\s-]?\d)",
    re.IGNORECASE,
)
# Masked forms:  ****1234   xxxx-1234   ••1234
_MASKED_RE = re.compile(r"(?:[*x•]{2,}[\s-]?){1,4}(\d{4})\b", re.IGNORECASE)
# A long bare number sitting next to account/card wording — we mask it to 4.
_LONG_NUMBER_RE = re.compile(
    r"(?:account|acct|a/c|card|iban|routing|sort\s*code)\D{0,15}?(\d[\d\s-]{9,})",
    re.IGNORECASE,
)


def mask_number(digits: str) -> str:
    """Return the last four digits of a number, nothing more."""
    only = re.sub(r"\D", "", digits or "")
    return only[-4:] if len(only) >= 4 else ""


def extract_account_refs(text: str) -> list[AccountRef]:
    """Find safe (last-four-only) account/card references in ``text``.

    Any full-looking number that appears next to account wording is reduced to
    its last four digits here, so nothing downstream ever sees the whole thing.
    """
    if not text:
        return []

    seen: set[str] = set()
    refs: list[AccountRef] = []

    def add(last4: str, original: str) -> None:
        if last4 and last4 not in seen:
            seen.add(last4)
            refs.append(AccountRef(last4=last4, original_text=original.strip()))

    for m in _MASKED_RE.finditer(text):
        add(m.group(1), m.group(0))
    for m in _ENDING_RE.finditer(text):
        add(m.group(1), m.group(0))
    for m in _LONG_NUMBER_RE.finditer(text):
        add(mask_number(m.group(1)), "account ending " + mask_number(m.group(1)))

    return refs


__all__ = (
    "AccountRef",
    "MoneyAmount",
    "extract_account_refs",
    "extract_money",
    "mask_number",
    "primary_money",
)
