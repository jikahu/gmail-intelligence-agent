"""The classification taxonomy (CLAUDE.md §6) and what each label implies.

Two ideas live here:

* :class:`Label` — the 18 ``AI/*`` labels. More than one can apply to a single
  email; the taxonomy is deliberately not single-label.
* :class:`LabelPolicy` — what each label *wants* to happen to the message in
  Gmail. The engine combines the policies of every applied label with
  :func:`combine_policies`, which always resolves conflicts in the safer
  direction: **if any label wants the message left in the Inbox, it stays.**

Nothing here talks to Gmail. These are intentions; Phase 11 is what acts on
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Priority(str, Enum):
    """Priority is independent of category (CLAUDE.md §7)."""

    P1_URGENT = "P1"
    P2_IMPORTANT = "P2"
    P3_NORMAL = "P3"

    @property
    def rank(self) -> int:
        """Lower is more urgent — useful for sorting and for ``min()``."""
        return {"P1": 1, "P2": 2, "P3": 3}[self.value]


def most_urgent(*priorities: Priority) -> Priority:
    """Return the most urgent of the given priorities."""
    return min(priorities, key=lambda p: p.rank)


class Label(str, Enum):
    """The ``AI/*`` label set. Values are the literal Gmail label names."""

    CRITICAL = "AI/Critical"
    ACTION_REQUIRED = "AI/Action-Required"
    PERSONAL = "AI/Personal"
    WORK_BUSINESS = "AI/Work-Business"
    PURCHASES_RECEIPTS = "AI/Purchases-Receipts"
    NEWSLETTER = "AI/Newsletter"
    LOW_VALUE = "AI/Low-Value"
    TRASH_CANDIDATE = "AI/Trash-Candidate"
    REVIEW = "AI/Review"
    EDUCATION = "AI/Education"
    SECURITY = "AI/Security"
    FINANCIAL = "AI/Financial"
    CAREER = "AI/Career"
    SUSPICIOUS = "AI/Suspicious"
    IMPORTANT_DOCUMENT = "AI/Important-Document"
    WAITING_FOR_REPLY = "AI/Waiting-For-Reply"
    SUBSCRIPTION_REVIEW = "AI/Subscription-Review"
    EXPIRED = "AI/Expired"


@dataclass(frozen=True)
class LabelPolicy:
    """What one label implies for the message's placement in Gmail.

    ``keep_in_inbox`` and ``archive`` are deliberately separate rather than one
    flag: a label may care about neither, and "no opinion" must not read as
    "archive it".
    """

    keep_in_inbox: bool = False
    archive: bool = False
    mark_important: bool = False
    #: False for labels that exist only as internal analysis (never sent to Gmail).
    applied_to_gmail: bool = True
    note: str = ""


#: Per-label policy, transcribed from CLAUDE.md §6.
LABEL_POLICIES: dict[Label, LabelPolicy] = {
    Label.CRITICAL: LabelPolicy(keep_in_inbox=True, mark_important=True),
    Label.ACTION_REQUIRED: LabelPolicy(keep_in_inbox=True, mark_important=True),
    Label.PERSONAL: LabelPolicy(keep_in_inbox=True),
    Label.WORK_BUSINESS: LabelPolicy(keep_in_inbox=True),
    Label.PURCHASES_RECEIPTS: LabelPolicy(
        archive=True, note="Archived but preserved; never a Review candidate."
    ),
    # Deliberately neutral. CLAUDE.md §6 keeps *Substack and approved senders*,
    # not newsletters in general — so the "keep in Inbox" decision belongs to
    # the engine, which knows whether this particular sender is approved. If
    # this label asserted keep_in_inbox, a course email that happens to carry
    # list headers would be pulled out of its own category's filing.
    Label.NEWSLETTER: LabelPolicy(
        note="Substack and approved senders are kept; others are routed to Review.",
    ),
    Label.LOW_VALUE: LabelPolicy(archive=True),
    Label.TRASH_CANDIDATE: LabelPolicy(
        applied_to_gmail=False,
        note=(
            "Internal analytic concept only (CLAUDE.md §6). Never applied to "
            "Gmail and never causes a Trash action."
        ),
    ),
    Label.REVIEW: LabelPolicy(archive=True, note="Archived, never deleted."),
    Label.EDUCATION: LabelPolicy(
        archive=True, note="Archived unless an action or deadline is present."
    ),
    Label.SECURITY: LabelPolicy(keep_in_inbox=True, mark_important=True),
    Label.FINANCIAL: LabelPolicy(keep_in_inbox=True),
    Label.CAREER: LabelPolicy(keep_in_inbox=True),
    Label.SUSPICIOUS: LabelPolicy(
        archive=True,
        note="Always paired with AI/Review. Links and attachments are never opened.",
    ),
    Label.IMPORTANT_DOCUMENT: LabelPolicy(
        note="Preserve the original message and its attachment."
    ),
    Label.WAITING_FOR_REPLY: LabelPolicy(keep_in_inbox=True),
    Label.SUBSCRIPTION_REVIEW: LabelPolicy(note="Never auto-cancels anything."),
    Label.EXPIRED: LabelPolicy(archive=True, note="Paired with AI/Review."),
}

#: Labels that mean "this email matters" — used by the Review veto and by the
#: acceptance gate in CLAUDE.md §15.
IMPORTANT_LABELS: frozenset[Label] = frozenset(
    {
        Label.CRITICAL,
        Label.ACTION_REQUIRED,
        Label.PERSONAL,
        Label.WORK_BUSINESS,
        Label.SECURITY,
        Label.FINANCIAL,
        Label.CAREER,
        Label.IMPORTANT_DOCUMENT,
        Label.PURCHASES_RECEIPTS,
    }
)


@dataclass(frozen=True)
class CombinedPolicy:
    """The resolved intent for a message carrying several labels."""

    keep_in_inbox: bool
    archive: bool
    mark_important: bool

    @property
    def describes_no_change(self) -> bool:
        return not (self.archive or self.mark_important)


def combine_policies(labels: set[Label]) -> CombinedPolicy:
    """Resolve several labels into one intent, erring toward visibility.

    The tie-break rule is the whole point: *keeping an email visible beats
    tidying it away* (CLAUDE.md §21). So a message labelled both
    ``AI/Purchases-Receipts`` (archive) and ``AI/Action-Required`` (inbox)
    stays in the Inbox.
    """
    policies = [LABEL_POLICIES[label] for label in labels]
    keep_in_inbox = any(p.keep_in_inbox for p in policies)
    return CombinedPolicy(
        keep_in_inbox=keep_in_inbox,
        # An archive intent only survives if nothing wants the message visible.
        archive=any(p.archive for p in policies) and not keep_in_inbox,
        mark_important=any(p.mark_important for p in policies),
    )


def gmail_labels(labels: set[Label]) -> list[str]:
    """Return the label names that may actually be written to Gmail, sorted.

    Filters out internal-only concepts such as ``AI/Trash-Candidate``.
    """
    return sorted(
        label.value for label in labels if LABEL_POLICIES[label].applied_to_gmail
    )


__all__ = (
    "CombinedPolicy",
    "IMPORTANT_LABELS",
    "LABEL_POLICIES",
    "Label",
    "LabelPolicy",
    "Priority",
    "combine_policies",
    "gmail_labels",
    "most_urgent",
)
