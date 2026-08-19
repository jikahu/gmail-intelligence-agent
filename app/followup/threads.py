"""Thread-level follow-up: Waiting for Reply and Overdue Action (CLAUDE.md §10).

Both are questions about a *thread's current state*, not a single message:

* **Waiting for Reply** — the user sent something that reasonably expects an
  answer, it's the most recent message in the thread, and three business days
  have passed with no reply.
* **Overdue Action** — an incoming message that needs the user's action is the
  most recent in the thread (they haven't replied), and it's been sitting for
  three business days.

Because both look at "the most recent message", they clear themselves: once the
other party replies, the latest message is no longer the user's, so Waiting for
Reply stops being emitted; once the user replies, Overdue Action stops. There's
no stored flag to reset — the next scan just recomputes (CLAUDE.md §13).
"""

from __future__ import annotations

import re
from datetime import date

from app.classification.labels import Label
from app.classification.message import EmailMessage
from app.followup import businessdays
from app.followup.businessdays import FOLLOWUP_BUSINESS_DAYS
from app.followup.models import FollowUpItem, FollowUpKind

#: Wording that marks a message as expecting a response.
_REQUEST_CUE = re.compile(
    r"\?|can you|could you|would you|will you|are you able|"
    r"please\s+(?:let me know|send|confirm|advise|review|reply|respond|update)|"
    r"let me know|what do you think|your thoughts|any update|"
    r"get back to me|need your|when can|by when|awaiting your|"
    r"looking forward to your",
    re.IGNORECASE,
)

#: A short closing or acknowledgement doesn't expect a reply.
_CLOSING = re.compile(
    r"^(thanks|thank you|thx|got it|sounds good|no problem|cheers|ok|okay|"
    r"great|will do|received|noted|perfect|awesome|much appreciated|"
    r"understood|see you|talk soon)[\s!.,]*$",
    re.IGNORECASE,
)


def expects_reply(message: EmailMessage) -> bool:
    """Does this user-sent message reasonably expect a response?

    Conservative on purpose: we'd rather miss a genuine wait than nag about a
    message that was only ever an acknowledgement (CLAUDE.md §10: "don't flag
    messages that clearly don't need a response").
    """
    if not message.to:
        return False
    if len(message.to) > 10:
        return False  # a broadcast, not a one-to-one ask
    core = (message.snippet or message.body_text or message.subject or "").strip()
    if _CLOSING.match(core):
        return False
    text = f"{message.subject} {message.snippet} {message.body_text}"
    return bool(_REQUEST_CUE.search(text))


def _latest_dated(messages: list[EmailMessage]) -> EmailMessage | None:
    dated = [m for m in messages if m.date is not None]
    if not dated:
        return None
    return max(dated, key=lambda m: m.date)


def evaluate_thread(
    messages: list[EmailMessage],
    today: date,
    action_required: dict[str, bool] | None = None,
) -> tuple[list[FollowUpItem], list[FollowUpItem]]:
    """Return ``(waiting_for_reply, overdue_actions)`` for one thread's messages."""
    action_required = action_required or {}
    last = _latest_dated(messages)
    if last is None:
        return [], []

    last_day = last.date.date()
    elapsed = businessdays.business_days_between(last_day, today)
    if elapsed < FOLLOWUP_BUSINESS_DAYS:
        return [], []

    waiting: list[FollowUpItem] = []
    overdue_actions: list[FollowUpItem] = []

    if last.sent_by_user and expects_reply(last):
        waiting.append(
            FollowUpItem(
                kind=FollowUpKind.WAITING_FOR_REPLY,
                message_id=last.message_id,
                thread_id=last.thread_id,
                subject=last.subject,
                reason=(
                    f"You sent this on {last_day.isoformat()} and haven't had a "
                    f"reply in {elapsed} business days."
                ),
                since=last_day.isoformat(),
                business_days_elapsed=elapsed,
                proposed_label=Label.WAITING_FOR_REPLY.value,
            )
        )

    if not last.sent_by_user and action_required.get(last.message_id):
        overdue_actions.append(
            FollowUpItem(
                kind=FollowUpKind.OVERDUE_ACTION,
                message_id=last.message_id,
                thread_id=last.thread_id,
                subject=last.subject,
                reason=(
                    f"This has needed your action since {last_day.isoformat()} — "
                    f"{elapsed} business days and still awaiting your response."
                ),
                since=last_day.isoformat(),
                business_days_elapsed=elapsed,
                proposed_label=Label.ACTION_REQUIRED.value,
            )
        )

    return waiting, overdue_actions


__all__ = ("evaluate_thread", "expects_reply")
