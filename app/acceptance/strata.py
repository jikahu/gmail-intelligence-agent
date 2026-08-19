"""The stratified-sample category buckets (CLAUDE.md §15).

CLAUDE.md §15 names the mix the 250-email acceptance sample must deliberately
include: "financial, security, government, personal, work, career, receipts,
purchases, travel, educational, Substack, other newsletters, promotions,
automated notifications, cold outreach, messages with attachments, active
threads, suspicious-looking messages."

**Honesty note.** Gmail search cannot cleanly target every one of those —
there's no query for "an email a human would call personal" or "a thread with
back-and-forth in it." Each :class:`~app.acceptance.models.Stratum` below is a
best-effort approximation using real Gmail search operators, not an exact
partition. Two buckets are explicitly weaker than the rest:

* ``personal`` / ``work`` share the same query (exclude Gmail's own bulk
  categories) — search can't tell personal from work correspondence apart,
  so both rely on the classifier's own labeling to separate them afterward,
  not the search itself.
* ``active_threads`` has no query at all (``""`` falls through to a plain
  recent-mail pull in :mod:`app.acceptance.service`) — Gmail search has no
  "this thread has multiple messages" operator, and the app doesn't fetch
  full threads for a scan-style run yet (the same known limitation the
  dashboard and ``/classify/preview`` already carry — see TECHNICAL_STATUS.md).

A ``catch_all`` bucket with no query tops the sample up toward the 250
target with a plain, unfiltered recent-mail pull, which is also where
``active_threads`` examples are most likely to actually show up.
"""

from __future__ import annotations

from app.acceptance.models import Stratum

#: The 18 named categories from CLAUDE.md §15, plus a catch-all topper.
STRATA: tuple[Stratum, ...] = (
    Stratum(
        "financial",
        'from:(bank OR chase OR paypal OR "wells fargo" OR "bank of america" OR statement) '
        "OR subject:(statement OR balance OR \"account summary\" OR transaction)",
        target=15,
        purpose="Bank/investment statements, balances, transactions.",
    ),
    Stratum(
        "security",
        'subject:("security alert" OR "suspicious sign-in" OR "new sign-in" OR '
        '"verify your account" OR "password reset" OR "two-factor")',
        target=12,
        purpose="Security alerts and account-access notices.",
    ),
    Stratum(
        "government",
        'subject:(IRS OR "tax return" OR "tax refund" OR government OR passport OR '
        '"social security")',
        target=8,
        purpose="Tax, government, and official correspondence.",
    ),
    Stratum(
        "personal",
        "-category:promotions -category:social -category:forums -category:updates",
        target=15,
        purpose="Individually-addressed mail (search can't separate personal from work — see module docstring).",
    ),
    Stratum(
        "work",
        "-category:promotions -category:social -category:forums -category:updates",
        target=15,
        purpose="Same query as personal; the classifier's own labels separate them afterward.",
    ),
    Stratum(
        "career",
        'subject:(interview OR "job offer" OR application OR recruiter OR '
        '"your application" OR hiring)',
        target=8,
        purpose="Job applications, interviews, recruiter outreach.",
    ),
    Stratum(
        "receipts",
        'subject:(receipt OR "order confirmation" OR "your order" OR invoice)',
        target=12,
        purpose="Purchase receipts and invoices.",
    ),
    Stratum(
        "purchases",
        "subject:(order OR shipped OR delivery OR tracking OR purchase)",
        target=12,
        purpose="Order and delivery updates.",
    ),
    Stratum(
        "travel",
        'subject:(itinerary OR "boarding pass" OR reservation OR flight OR '
        '"your trip" OR hotel)',
        target=10,
        purpose="Flights, hotels, reservations, itineraries.",
    ),
    Stratum(
        "educational",
        "subject:(course OR lecture OR enrollment OR certificate OR class OR university)",
        target=10,
        purpose="Genuine educational content.",
    ),
    Stratum(
        "substack",
        "from:substack.com",
        target=8,
        purpose="Substack newsletters — always protected (CLAUDE.md §9).",
    ),
    Stratum(
        "other_newsletters",
        "unsubscribe -from:substack.com",
        target=12,
        purpose="Non-Substack newsletters — default to Review unless approved.",
    ),
    Stratum(
        "promotions",
        "category:promotions",
        target=15,
        purpose="Gmail's own Promotions category — a strong Review signal.",
    ),
    Stratum(
        "automated_notifications",
        "category:updates OR category:forums OR from:(noreply OR no-reply OR notifications)",
        target=12,
        purpose="Repetitive automated notifications.",
    ),
    Stratum(
        "cold_outreach",
        'subject:("quick question" OR "following up" OR "reaching out" OR '
        'partnership OR "grow your")',
        target=8,
        purpose="Cold sales outreach.",
    ),
    Stratum(
        "attachments",
        "has:attachment",
        target=12,
        purpose="Messages with attachments — hard-protected (CLAUDE.md §8).",
    ),
    Stratum(
        "active_threads",
        "",
        target=10,
        purpose="No usable search operator — see module docstring; falls back to recent mail.",
    ),
    Stratum(
        "suspicious",
        'subject:("verify your account" OR "unusual activity" OR suspended OR '
        '"confirm your identity" OR "unusual sign-in activity")',
        target=8,
        purpose="Suspicious/phishing-looking messages.",
    ),
    Stratum(
        "catch_all",
        "",
        target=48,
        purpose="Plain recent mail, to reach the 250 target and provide a realistic baseline mix.",
    ),
)

#: Sum of every stratum's target — the default sample size.
DEFAULT_SAMPLE_TARGET: int = sum(s.target for s in STRATA)


__all__ = ("DEFAULT_SAMPLE_TARGET", "STRATA")
