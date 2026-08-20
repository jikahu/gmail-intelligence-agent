"""Turn a :class:`~app.classification.engine.Classification` into real Gmail
API calls (CLAUDE.md §11 step 10 — "the rules engine decides, this module
acts").

Two ideas live here:

* :func:`check_write_gate` — the single switch every write path in the app
  must check first. Both of these must be true, or nothing is written:
  ``DRY_RUN=false`` and ``GMAIL_PROCESSING_ENABLED=true``.
* :func:`plan_change` / :func:`apply_to_message` — compute the minimal label
  diff between what Gmail currently shows and what the classification wants,
  then issue exactly one ``messages.modify`` call for it. Idempotent: a
  message already in its desired state produces an empty plan and no API
  call at all, which matters since the real-time poller re-runs this on the
  same mail repeatedly.

What this module will never do, structurally:

* Never marks Important, then removes it. Only ever adds ``IMPORTANT`` — an
  automated pass has no business taking away a signal a human or Gmail's own
  ML set (mirrors the AI layer's own "can raise, never lower" guarantee).
* Never calls ``trash`` on its own. Trashing is never automatic — the
  automatic apply path only ever touches the taxonomy's labels, ``INBOX``,
  ``IMPORTANT``, and (additively only) an existing label the user already
  made by hand (see :mod:`app.gmail.vendor_labels`).
* Never touches ``Trash-Candidate`` — it isn't in ``gmail_label_names`` to
  begin with (CLAUDE.md §6: internal analytic concept only).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.classification.engine import Classification
from app.classification.labels import Label
from app.classification.message import EmailMessage
from app.config import get_settings
from app.gmail.vendor_labels import match_existing_label
from app.gmail.write_client import IMPORTANT_LABEL, INBOX_LABEL, GmailWriteClient
from app.logging_config import get_logger

log = get_logger("app.gmail.apply")

#: Every label name the taxonomy itself might write to Gmail — used to tell
#: "one of ours" apart from a label the user made by hand (CLAUDE.md §6).
TAXONOMY_LABEL_NAMES: frozenset[str] = frozenset(label.value for label in Label)


@dataclass(frozen=True)
class WriteGateStatus:
    """Whether live Gmail writes are currently allowed, and why not if not."""

    allowed: bool
    reasons: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def check_write_gate() -> WriteGateStatus:
    """The one function every Gmail-write code path must consult first."""
    settings = get_settings()
    reasons: list[str] = []

    if settings.dry_run:
        reasons.append("DRY_RUN is true (dry-run mode) — set it to false to allow writes")
    if not settings.gmail_processing_enabled:
        reasons.append("GMAIL_PROCESSING_ENABLED is false")

    return WriteGateStatus(allowed=not reasons, reasons=tuple(reasons))


def fetch_current_labels(message_id: str) -> list[str]:
    """The message's real, live label state — a cheap ``minimal``-format
    read, not the full message. Used by every write path that needs to
    diff against *current* Gmail state rather than a possibly-stale
    snapshot: single-message dashboard actions and Undo (Phase 12) both
    need this, not just the classification-driven batch apply path.
    """
    from app.gmail.client import get_client

    raw = get_client().get_message(message_id, message_format="minimal")
    return list(raw.get("labelIds") or [])


@dataclass(frozen=True)
class ChangePlan:
    """The minimal set of Gmail API changes needed to reach a classification's
    desired state from a message's current, real label set."""

    message_id: str
    add_label_ids: tuple[str, ...] = ()
    remove_label_ids: tuple[str, ...] = ()
    #: Human-readable label *names* (not ids) — for the audit row and UI.
    add_label_names: tuple[str, ...] = ()
    remove_label_names: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.add_label_ids and not self.remove_label_ids


@dataclass(frozen=True)
class AppliedChange:
    """What actually happened (or would happen, in a dry preview) to one
    message — enough detail for a real ``Audit_Log`` row (CLAUDE.md §13)."""

    message_id: str
    thread_id: str
    changed: bool
    labels_before: tuple[str, ...]
    labels_after: tuple[str, ...]
    inbox_before: bool
    inbox_after: bool
    action_taken: str


def plan_change(
    message: EmailMessage,
    classification: Classification,
    label_name_to_id: dict[str, str],
    vendor_label_name: str | None = None,
) -> ChangePlan:
    """Compute the label diff, without calling Gmail.

    ``label_name_to_id`` must already contain an entry for every taxonomy
    label the classification wants (see
    :meth:`GmailWriteClient.ensure_labels`) plus the two Gmail system labels
    this module touches, ``INBOX`` and ``IMPORTANT`` (whose id equals their
    name, so callers may simply include them as self-mapped entries), and —
    when given — ``vendor_label_name``, an existing label the user already
    made by hand (:mod:`app.gmail.vendor_labels`).
    """
    current = set(message.label_ids)
    current_ai_names = current & TAXONOMY_LABEL_NAMES
    desired_ai_names = set(classification.gmail_label_names)

    add_names = desired_ai_names - current_ai_names
    remove_names = current_ai_names - desired_ai_names

    # A vendor-matched label is additive only, never removed by this app —
    # it belongs to the user, not the taxonomy, so absence from a future
    # classification must never be read as "take it away".
    if vendor_label_name and vendor_label_name not in current:
        add_names.add(vendor_label_name)

    # INBOX: only touched when the classification has an actual opinion.
    # "Neither keep nor archive" must leave the message exactly where it is.
    if classification.keep_in_inbox and INBOX_LABEL not in current:
        add_names.add(INBOX_LABEL)
    elif classification.archive and INBOX_LABEL in current:
        remove_names.add(INBOX_LABEL)

    # IMPORTANT: add-only, never removed automatically.
    if classification.mark_important and IMPORTANT_LABEL not in current:
        add_names.add(IMPORTANT_LABEL)

    add_ids = tuple(label_name_to_id[name] for name in sorted(add_names))
    remove_ids = tuple(label_name_to_id[name] for name in sorted(remove_names))

    return ChangePlan(
        message_id=message.message_id,
        add_label_ids=add_ids,
        remove_label_ids=remove_ids,
        add_label_names=tuple(sorted(add_names)),
        remove_label_names=tuple(sorted(remove_names)),
    )


def describe_plan(plan: ChangePlan) -> str:
    if plan.is_empty:
        return "no change — already in the desired state"
    parts = []
    if plan.add_label_names:
        parts.append(f"added {', '.join(plan.add_label_names)}")
    if plan.remove_label_names:
        parts.append(f"removed {', '.join(plan.remove_label_names)}")
    return "; ".join(parts)


def apply_to_message(
    client: GmailWriteClient,
    message: EmailMessage,
    classification: Classification,
    label_name_to_id: dict[str, str],
    vendor_label_name: str | None = None,
) -> AppliedChange:
    """Compute the plan and execute it as one ``modify`` call, if non-empty.

    Callers must have already confirmed :func:`check_write_gate` allows this.
    """
    plan = plan_change(message, classification, label_name_to_id, vendor_label_name)
    labels_before = tuple(sorted(message.label_ids))
    inbox_before = INBOX_LABEL in message.label_ids

    if plan.is_empty:
        return AppliedChange(
            message_id=message.message_id,
            thread_id=message.thread_id,
            changed=False,
            labels_before=labels_before,
            labels_after=labels_before,
            inbox_before=inbox_before,
            inbox_after=inbox_before,
            action_taken=describe_plan(plan),
        )

    response = client.modify_message(
        message.message_id,
        add_label_ids=list(plan.add_label_ids),
        remove_label_ids=list(plan.remove_label_ids),
    )
    # Trust Gmail's own returned state rather than reconstructing it from the
    # plan — the API response is the actual source of truth for what changed.
    labels_after = tuple(sorted(response.get("labelIds") or []))
    inbox_after = INBOX_LABEL in labels_after

    log.info(
        "gmail_message_modified",
        extra={
            "message_id": message.message_id,
            "added": plan.add_label_names,
            "removed": plan.remove_label_names,
        },
    )

    return AppliedChange(
        message_id=message.message_id,
        thread_id=message.thread_id,
        changed=True,
        labels_before=labels_before,
        labels_after=labels_after,
        inbox_before=inbox_before,
        inbox_after=inbox_after,
        action_taken=describe_plan(plan),
    )


def label_name_map_for(
    client: GmailWriteClient,
    classifications: list[Classification],
    vendor_label_names: set[str] | None = None,
) -> dict[str, str]:
    """Resolve/create every taxonomy label a batch of classifications needs,
    plus the two system labels and any matched vendor labels, in one label
    listing (see :meth:`GmailWriteClient.ensure_labels`).

    Vendor labels always already exist by construction
    (:func:`app.gmail.vendor_labels.match_existing_label` only ever returns a
    name already present in Gmail), so including them here resolves their id
    without ``ensure_labels`` ever creating anything new for them.
    """
    names: set[str] = {INBOX_LABEL, IMPORTANT_LABEL}
    for classification in classifications:
        names.update(classification.gmail_label_names)
    names.update(vendor_label_names or set())
    ensured = client.ensure_labels(sorted(names))
    # INBOX/IMPORTANT are system labels; their id is always their name.
    ensured.setdefault(INBOX_LABEL, INBOX_LABEL)
    ensured.setdefault(IMPORTANT_LABEL, IMPORTANT_LABEL)
    return ensured


def vendor_label_for(client: GmailWriteClient, message: EmailMessage) -> str | None:
    """Look up an existing Gmail label the user already made that this
    message's sender matches (see :mod:`app.gmail.vendor_labels`)."""
    return match_existing_label(client.label_names(), message)


__all__ = (
    "TAXONOMY_LABEL_NAMES",
    "AppliedChange",
    "ChangePlan",
    "WriteGateStatus",
    "apply_to_message",
    "check_write_gate",
    "describe_plan",
    "fetch_current_labels",
    "label_name_map_for",
    "plan_change",
    "vendor_label_for",
)
