"""Keyword and phrase sets used by the deterministic rules.

**The asymmetry here is deliberate.** These lists are not balanced, and
shouldn't be:

* Sets that *protect* an email (financial, medical, legal, travel, security…)
  are written generously. A false positive costs nothing — the message simply
  stays visible. A false negative could hide something that matters.
* Sets that route an email to *Review* (promotional, cold sales, engagement
  bait…) are written conservatively. A false positive here is the exact failure
  CLAUDE.md §15 forbids.

When in doubt, add the phrase to a protection set and leave the Review sets
alone. Protection also structurally outranks Review in the engine, so a phrase
appearing in both lists resolves safely.
"""

from __future__ import annotations

import re
from typing import Iterable


class PatternSet:
    """A named group of phrases, matched case-insensitively on word boundaries."""

    __slots__ = ("name", "phrases", "_regex")

    def __init__(self, name: str, phrases: Iterable[str]) -> None:
        self.name = name
        self.phrases: tuple[str, ...] = tuple(dict.fromkeys(phrases))
        # Boundaries are applied per phrase, not around the whole alternation.
        # A phrase like "% off" starts with a non-word character, and a shared
        # `(?<!\w)` prefix would stop it matching in "50% off" — the digit
        # before it is a word character. Same story at the end for "order #".
        parts: list[str] = []
        # Longest first so "payment failed" wins over "payment" in the report.
        for phrase in sorted(self.phrases, key=len, reverse=True):
            escaped = re.escape(phrase)
            prefix = r"(?<!\w)" if phrase[:1].isalnum() or phrase[:1] == "_" else ""
            suffix = r"(?!\w)" if phrase[-1:].isalnum() or phrase[-1:] == "_" else ""
            parts.append(f"{prefix}{escaped}{suffix}")
        self._regex = re.compile("|".join(parts), re.IGNORECASE)

    def first_match(self, text: str) -> str | None:
        """Return the first matching phrase, or ``None``."""
        if not text:
            return None
        found = self._regex.search(text)
        return found.group(0).lower() if found else None

    def matches(self, text: str) -> bool:
        return self.first_match(text) is not None

    def all_matches(self, text: str) -> list[str]:
        if not text:
            return []
        return sorted({m.group(0).lower() for m in self._regex.finditer(text)})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PatternSet({self.name!r}, {len(self.phrases)} phrases)"


# --------------------------------------------------------------------
# Protection topics (CLAUDE.md §8) — generous by design
# --------------------------------------------------------------------

SECURITY = PatternSet(
    "security",
    (
        "security alert", "security warning", "security notice",
        "suspicious activity", "suspicious sign-in", "suspicious login",
        "unusual activity", "unusual sign-in", "unusual login",
        "new sign-in", "new login", "new device", "unrecognized device",
        "sign-in attempt", "login attempt", "failed login",
        "password reset", "reset your password", "change your password",
        "password was changed", "password has been changed",
        "two-factor", "2fa", "verification code", "security code",
        "one-time code", "one-time password", "authentication code",
        "account locked", "account suspended", "account disabled",
        "unauthorized access", "unauthorised access", "data breach",
        "compromised", "verify your identity", "identity verification",
        "recovery email", "recovery phone", "backup codes",
    ),
)

FRAUD = PatternSet(
    "fraud",
    (
        "fraud alert", "fraudulent", "fraud detected", "possible fraud",
        "unauthorized transaction", "unauthorised transaction",
        "unauthorized charge", "unrecognized transaction",
        "did you make this", "confirm this transaction",
        "card has been blocked", "card was declined", "freeze your card",
    ),
)

FINANCIAL = PatternSet(
    "financial",
    (
        "bank", "banking", "account statement", "statement is ready",
        "monthly statement", "e-statement", "estatement",
        "balance", "available balance", "current balance",
        "transaction", "transactions", "transfer", "wire transfer",
        "direct debit", "standing order", "deposit", "withdrawal",
        "payment", "payments", "payment received", "payment due",
        "payment failed", "payment declined", "payment unsuccessful",
        "invoice", "invoices", "bill", "billing", "amount due",
        "minimum payment", "credit card", "debit card", "overdraft",
        "loan", "mortgage", "interest rate", "apr",
        "investment", "portfolio", "dividend", "brokerage",
        "retirement", "pension", "401k", "ira contribution",
        "refund", "reimbursement", "payout", "remittance",
        "tax", "taxes", "tax return", "irs", "kra", "hmrc",
        "w-2", "w2", "1099", "p60", "paye",
        "payroll", "payslip", "pay stub", "salary",
    ),
)

LEGAL_GOVERNMENT = PatternSet(
    "legal_government",
    (
        "legal notice", "legal action", "attorney", "solicitor", "lawyer",
        "court", "subpoena", "summons", "litigation", "settlement",
        "contract", "agreement", "terms of service", "power of attorney",
        "government", "immigration", "visa", "passport", "embassy",
        "social security", "national id", "national insurance",
        "department of", "ministry of", "county clerk", "dmv",
        "jury duty", "notice of", "official notice",
    ),
)

MEDICAL = PatternSet(
    "medical",
    (
        "appointment", "doctor", "physician", "clinic", "hospital",
        "medical", "health record", "lab results", "test results",
        "prescription", "pharmacy", "refill", "diagnosis",
        "dental", "dentist", "optometrist", "specialist referral",
        "patient portal", "medical bill", "explanation of benefits",
        "vaccination", "immunization", "screening",
    ),
)

INSURANCE = PatternSet(
    "insurance",
    (
        "insurance", "policy", "policy number", "premium", "coverage",
        "deductible", "claim", "claims", "underwriting", "renewal notice",
        "beneficiary", "life insurance", "health insurance", "auto insurance",
        "home insurance", "renters insurance",
    ),
)

TRAVEL = PatternSet(
    "travel",
    (
        "flight", "flights", "boarding pass", "check-in", "checked in",
        "itinerary", "departure", "arrival", "gate change", "delayed flight",
        "booking", "booking confirmation", "reservation", "reserved",
        "hotel", "accommodation", "check-out", "airbnb",
        "car rental", "rental car", "pick-up location",
        "confirmation number", "booking reference", "pnr",
        "e-ticket", "eticket", "ticket confirmation", "travel",
        "baggage", "seat assignment", "trip",
    ),
)

PURCHASE = PatternSet(
    "purchase",
    (
        "order confirmation", "order confirmed", "your order", "order number",
        "order #", "purchase", "purchased", "receipt", "your receipt",
        "thank you for your order", "thanks for your order",
        "we received your order", "order summary", "order details",
    ),
)

DELIVERY = PatternSet(
    "delivery",
    (
        "shipped", "has shipped", "on its way", "out for delivery",
        "delivered", "delivery", "tracking number", "track your package",
        "shipment", "dispatched", "parcel", "courier",
        "delivery attempt", "delivery delayed", "arriving today",
    ),
)

EDUCATION = PatternSet(
    "education",
    (
        "course", "lesson", "module", "curriculum", "syllabus",
        "assignment", "homework", "coursework", "quiz", "exam",
        "lecture", "tutorial", "workshop", "certificate of completion",
        "certification", "enrollment", "enrolment", "semester",
        "transcript", "grade", "grades", "student", "instructor",
        "university", "college", "school", "academy",
    ),
)

CAREER = PatternSet(
    "career",
    (
        "job", "job application", "application received", "applied for",
        "interview", "interview invitation", "phone screen",
        "recruiter", "recruiting", "hiring", "hiring manager",
        "offer letter", "job offer", "position", "role", "vacancy",
        "candidate", "resume", "cv", "cover letter",
        "background check", "reference check", "onboarding",
    ),
)

CALENDAR = PatternSet(
    "calendar",
    (
        "invitation", "calendar invite", "meeting invitation",
        "meeting", "event", "rsvp", "accepted", "declined", "tentative",
        "rescheduled", "cancelled", "canceled", "updated invitation",
        "when:", "where:", "google calendar", "webinar registration",
    ),
)

IMPORTANT_DOCUMENT = PatternSet(
    "important_document",
    (
        "certificate", "diploma", "warranty", "title deed", "deed",
        "official document", "signed copy", "executed copy",
        "annual report", "tax document", "policy document",
        "please find attached", "attached is", "attached please find",
    ),
)

SUBSCRIPTION = PatternSet(
    "subscription",
    (
        "subscription", "subscribe", "auto-renew", "auto renewal",
        "renews on", "renewal", "will renew", "membership",
        "your plan", "plan change", "upgrade your plan", "free trial",
        "trial ending", "trial expires", "billing cycle",
        "recurring payment", "recurring charge",
    ),
)

MATERIAL_CHANGE = PatternSet(
    "material_change",
    (
        "price increase", "price change", "new pricing", "rate change",
        "fee change", "new fee", "fee increase", "changes to your",
        "important changes", "update to our terms", "terms update",
        "updated terms", "policy update", "privacy policy update",
        "changes to your account", "service discontinued",
        "discontinuing", "will no longer", "end of life",
        "coverage change", "effective date",
    ),
)

# --------------------------------------------------------------------
# Urgency and action (CLAUDE.md §7)
# --------------------------------------------------------------------

P1_URGENT = PatternSet(
    "p1_urgent",
    (
        "urgent", "immediate action", "immediately", "act now",
        "action required today", "due today", "expires today",
        "final notice", "last notice", "overdue",
        "payment failed", "payment declined", "payment unsuccessful",
        "declined", "past due", "account locked", "account suspended",
        "flight cancelled", "flight canceled", "flight delayed",
        "gate change", "cancellation notice",
    ),
)

ACTION_REQUIRED = PatternSet(
    "action_required",
    (
        "action required", "action needed", "please confirm", "confirm your",
        "please respond", "respond by", "reply by", "please review",
        "please sign", "signature required", "please complete",
        "awaiting your", "we need your", "requires your attention",
        "please update", "update required", "submit by", "deadline",
        "due by", "due date", "due today", "due tomorrow", "is due",
        "must be submitted", "rsvp", "please verify",
    ),
)

# --------------------------------------------------------------------
# Review candidates (CLAUDE.md §9) — conservative by design
# --------------------------------------------------------------------

PROMOTIONAL = PatternSet(
    "promotional",
    (
        "sale", "flash sale", "on sale", "discount", "% off", "percent off",
        "coupon", "promo code", "voucher", "deal of the day", "best deals",
        "limited time offer", "special offer", "exclusive offer",
        "buy now", "shop now", "save big", "lowest price",
        "black friday", "cyber monday", "clearance", "bogo",
        "free shipping", "giveaway", "sweepstakes",
    ),
)

COLD_SALES = PatternSet(
    "cold_sales",
    (
        "quick question", "hop on a call", "book a demo", "schedule a demo",
        "free consultation", "grow your business", "boost your revenue",
        "increase your sales", "generate more leads", "lead generation",
        "just following up", "circling back", "bumping this",
        "did you get a chance", "let me know if you'd be open",
        "partnership opportunity", "collaboration opportunity",
    ),
)

ENGAGEMENT_BAIT = PatternSet(
    "engagement_bait",
    (
        "we miss you", "we've missed you", "come back", "still interested",
        "you might like", "recommended for you", "picked for you",
        "based on your", "trending now", "don't miss out",
        "you haven't logged in", "your weekly digest",
        "complete your profile", "finish signing up",
        "rate your experience", "take our survey", "quick survey",
        "tell us what you think", "how did we do",
    ),
)

CRYPTO_PROMO = PatternSet(
    "crypto_promo",
    (
        "crypto", "bitcoin", "ethereum", "nft", "web3", "token sale",
        "airdrop", "defi", "blockchain opportunity", "presale",
        "guaranteed returns", "passive income", "double your",
    ),
)

WEBINAR_PROMO = PatternSet(
    "webinar_promo",
    (
        "webinar", "masterclass", "free training", "live session",
        "register now", "save your seat", "reserve your spot",
        "join us live", "virtual summit",
    ),
)

EXPIRED = PatternSet(
    "expired",
    (
        "expired", "has expired", "no longer valid", "offer ended",
        "sale ended", "event has passed", "thanks for attending",
        "code expires", "expires in", "your code is", "your otp",
        "delivered successfully", "was delivered",
    ),
)

# --------------------------------------------------------------------
# Suspicious signals (CLAUDE.md §6 label 14)
# --------------------------------------------------------------------

PHISHING = PatternSet(
    "phishing",
    (
        "verify your account", "confirm your account", "validate your account",
        "your account will be closed", "your account will be suspended",
        "click here to verify", "click the link below to verify",
        "update your payment information", "update your billing",
        "confirm your password", "enter your password",
        "unusual sign in attempt was blocked",
        "you have won", "you've won", "claim your prize",
        "inheritance", "beneficiary of", "next of kin",
        "wire the funds", "gift card", "itunes card",
        "send bitcoin", "bank transfer urgently",
    ),
)

#: Top-level domains that overwhelmingly host throwaway senders.
SUSPICIOUS_TLDS: frozenset[str] = frozenset(
    {
        "zip", "mov", "top", "xyz", "click", "link", "gq", "cf", "tk", "ml",
        "work", "loan", "review", "country", "kim", "science", "party",
    }
)

#: Free/public mailbox providers. Approving one address at such a domain must
#: never trust the whole domain (CLAUDE.md §8).
PUBLIC_EMAIL_PROVIDERS: frozenset[str] = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk",
        "hotmail.com", "outlook.com", "live.com", "msn.com",
        "aol.com", "icloud.com", "me.com", "mac.com",
        "proton.me", "protonmail.com", "gmx.com", "gmx.net",
        "mail.com", "zoho.com", "yandex.com", "yandex.ru",
        "qq.com", "163.com", "126.com", "fastmail.com",
        "tutanota.com", "hushmail.com", "pm.me",
    }
)

#: Domains that publish newsletters we keep by default (CLAUDE.md §9).
SUBSTACK_DOMAINS: frozenset[str] = frozenset({"substack.com", "substackcdn.com"})


__all__ = (
    "ACTION_REQUIRED",
    "CALENDAR",
    "CAREER",
    "COLD_SALES",
    "CRYPTO_PROMO",
    "DELIVERY",
    "EDUCATION",
    "ENGAGEMENT_BAIT",
    "EXPIRED",
    "FINANCIAL",
    "FRAUD",
    "IMPORTANT_DOCUMENT",
    "INSURANCE",
    "LEGAL_GOVERNMENT",
    "MATERIAL_CHANGE",
    "MEDICAL",
    "P1_URGENT",
    "PHISHING",
    "PROMOTIONAL",
    "PUBLIC_EMAIL_PROVIDERS",
    "PURCHASE",
    "PatternSet",
    "SECURITY",
    "SUBSCRIPTION",
    "SUBSTACK_DOMAINS",
    "SUSPICIOUS_TLDS",
    "TRAVEL",
    "WEBINAR_PROMO",
)
