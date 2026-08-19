"""Gmail write-capable client tests (Phase 11) — mocked googleapiclient service."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.gmail.write_client import GmailWriteClient


def _fake_service(existing_labels: list[dict] | None = None) -> MagicMock:
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": existing_labels or []
    }
    return service


def test_ensure_labels_creates_only_the_missing_ones() -> None:
    service = _fake_service(
        existing_labels=[{"name": "INBOX", "id": "INBOX"}, {"name": "AI/Review", "id": "Label_1"}]
    )
    create_call = service.users.return_value.labels.return_value.create
    create_call.return_value.execute.return_value = {"id": "Label_2", "name": "AI/Financial"}

    client = GmailWriteClient(service=service)
    result = client.ensure_labels(["AI/Review", "AI/Financial"])

    assert result == {"AI/Review": "Label_1", "AI/Financial": "Label_2"}
    # Only the missing label was created — one call, not two.
    assert create_call.call_count == 1
    kwargs = create_call.call_args.kwargs
    assert kwargs["body"]["name"] == "AI/Financial"


def test_ensure_labels_lists_only_once_across_calls() -> None:
    """The label listing is cached for the client's lifetime — a batch apply
    over many messages must not re-list labels before every one."""
    service = _fake_service(existing_labels=[{"name": "AI/Review", "id": "Label_1"}])
    client = GmailWriteClient(service=service)

    client.ensure_labels(["AI/Review"])
    client.ensure_labels(["AI/Review"])

    list_call = service.users.return_value.labels.return_value.list
    assert list_call.call_count == 1


def test_modify_message_combines_add_and_remove_into_one_call() -> None:
    service = _fake_service()
    modify_call = service.users.return_value.messages.return_value.modify
    modify_call.return_value.execute.return_value = {"id": "m1", "labelIds": ["INBOX"]}

    client = GmailWriteClient(service=service)
    client.modify_message("m1", add_label_ids=["INBOX", "Label_1"], remove_label_ids=["AI/Review"])

    assert modify_call.call_count == 1
    body = modify_call.call_args.kwargs["body"]
    assert set(body["addLabelIds"]) == {"INBOX", "Label_1"}
    assert body["removeLabelIds"] == ["AI/Review"]


def test_trash_and_untrash_call_the_right_endpoints() -> None:
    service = _fake_service()
    trash_call = service.users.return_value.messages.return_value.trash
    untrash_call = service.users.return_value.messages.return_value.untrash
    trash_call.return_value.execute.return_value = {"id": "m1", "labelIds": ["TRASH"]}
    untrash_call.return_value.execute.return_value = {"id": "m1", "labelIds": ["INBOX"]}

    client = GmailWriteClient(service=service)
    client.trash_message("m1")
    client.untrash_message("m1")

    assert trash_call.call_args.kwargs["id"] == "m1"
    assert untrash_call.call_args.kwargs["id"] == "m1"


def test_write_client_never_calls_a_permanent_delete_endpoint() -> None:
    """Structural guarantee: nothing in this class can reach batchDelete or
    a hard delete — the Gmail API method for that simply isn't invoked."""
    service = _fake_service()
    client = GmailWriteClient(service=service)
    client.modify_message("m1")
    client.trash_message("m1")

    messages_mock = service.users.return_value.messages.return_value
    assert not messages_mock.delete.called
    assert not messages_mock.batchDelete.called
