"""The Phase 11 write gate and label-diff logic — the safety-critical half
of real Gmail writes. :mod:`app.gmail.write_client` calls the API;
:mod:`app.gmail.apply` decides *whether* and *what* to call it with, which
is where CLAUDE.md's safety rules actually live.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.classification.engine import Classification
from app.classification.labels import Label, Priority
from app.gmail import apply as gmail_apply
from tests.fixtures.emails import make_message


# --------------------------------------------------------------------
# check_write_gate
# --------------------------------------------------------------------


def test_gate_closed_by_default_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    status = gmail_apply.check_write_gate()
    assert status.allowed is False
    assert any("DRY_RUN" in r for r in status.reasons)


def test_gate_closed_when_processing_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "false")
    get_settings.cache_clear()
    status = gmail_apply.check_write_gate()
    assert status.allowed is False
    assert any("GMAIL_PROCESSING_ENABLED" in r for r in status.reasons)


def test_gate_open_when_both_conditions_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GMAIL_PROCESSING_ENABLED", "true")
    get_settings.cache_clear()
    status = gmail_apply.check_write_gate()
    assert status.allowed is True
    assert status.reasons == ()


# --------------------------------------------------------------------
# plan_change — the label diff
# --------------------------------------------------------------------


#: Every taxonomy label mapped to a synthetic id, plus the two system labels —
#: plan_change never needs a real Gmail connection to compute a diff.
_LABEL_MAP = {"INBOX": "INBOX", "IMPORTANT": "IMPORTANT"} | {
    label.value: f"Label_{i}" for i, label in enumerate(Label)
}


def test_plan_is_empty_when_already_in_the_desired_state() -> None:
    message = make_message(labels=["INBOX", "Financial"])
    decision = Classification(
        labels={Label.FINANCIAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    plan = gmail_apply.plan_change(message, decision, _LABEL_MAP)
    assert plan.is_empty


def test_plan_adds_missing_ai_label_and_inbox() -> None:
    message = make_message(labels=["Review"])  # not currently in Inbox
    decision = Classification(
        labels={Label.FINANCIAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    plan = gmail_apply.plan_change(message, decision, _LABEL_MAP)
    assert set(plan.add_label_names) == {"Financial", "INBOX"}
    assert "Review" in plan.remove_label_names  # no longer wanted


def test_plan_removes_inbox_only_when_classification_wants_archive() -> None:
    message = make_message(labels=["INBOX", "Review"])
    decision = Classification(
        labels={Label.REVIEW}, keep_in_inbox=False, archive=True, mark_important=False
    )
    plan = gmail_apply.plan_change(message, decision, _LABEL_MAP)
    assert "INBOX" in plan.remove_label_names


def test_plan_leaves_inbox_alone_when_classification_has_no_opinion() -> None:
    """keep_in_inbox=False and archive=False together mean 'no opinion' —
    CLAUDE.md: that must never be read as 'archive it'."""
    message = make_message(labels=["INBOX", "Newsletter"])
    decision = Classification(
        labels={Label.NEWSLETTER}, keep_in_inbox=False, archive=False, mark_important=False
    )
    plan = gmail_apply.plan_change(message, decision, _LABEL_MAP)
    assert "INBOX" not in plan.add_label_names
    assert "INBOX" not in plan.remove_label_names


def test_plan_adds_important_when_requested() -> None:
    message = make_message(labels=["INBOX"])
    decision = Classification(
        labels={Label.CRITICAL}, keep_in_inbox=True, archive=False, mark_important=True
    )
    plan = gmail_apply.plan_change(message, decision, _LABEL_MAP)
    assert "IMPORTANT" in plan.add_label_names


def test_plan_never_removes_important_automatically() -> None:
    """Add-only: an automated pass must never strip a signal a human or
    Gmail's own ML set on the message."""
    message = make_message(labels=["INBOX", "IMPORTANT"])
    decision = Classification(
        labels={Label.PERSONAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    plan = gmail_apply.plan_change(message, decision, _LABEL_MAP)
    assert "IMPORTANT" not in plan.remove_label_names


def test_plan_never_touches_trash_candidate() -> None:
    """Trash-Candidate is internal-only (CLAUDE.md §6) — it must never
    appear as a Gmail label to add, even when the engine assigned it."""
    message = make_message(labels=["INBOX"])
    decision = Classification(
        labels={Label.TRASH_CANDIDATE, Label.REVIEW},
        keep_in_inbox=False,
        archive=True,
        mark_important=False,
    )
    plan = gmail_apply.plan_change(message, decision, _LABEL_MAP)
    assert "Trash-Candidate" not in plan.add_label_names


# --------------------------------------------------------------------
# apply_to_message — executing the plan
# --------------------------------------------------------------------


def test_apply_to_message_skips_the_api_call_when_plan_is_empty() -> None:
    message = make_message(labels=["INBOX", "Financial"])
    decision = Classification(
        labels={Label.FINANCIAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    client = MagicMock()
    change = gmail_apply.apply_to_message(client, message, decision, _LABEL_MAP)
    assert change.changed is False
    assert not client.modify_message.called


def test_apply_to_message_issues_exactly_one_modify_call() -> None:
    message = make_message(labels=["Review"])
    decision = Classification(
        labels={Label.FINANCIAL}, keep_in_inbox=True, archive=False, mark_important=False
    )
    client = MagicMock()
    client.modify_message.return_value = {
        "id": message.message_id,
        "labelIds": ["INBOX", "Financial"],
    }
    change = gmail_apply.apply_to_message(client, message, decision, _LABEL_MAP)

    assert client.modify_message.call_count == 1
    assert change.changed is True
    assert change.labels_after == ("Financial", "INBOX")
    assert change.inbox_after is True
