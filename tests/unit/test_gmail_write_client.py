"""Gmail write-capable client tests (Phase 11) — mocked googleapiclient service."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.gmail.write_client import LABEL_COLORS, GmailWriteClient


def _fake_service(existing_labels: list[dict] | None = None) -> MagicMock:
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": existing_labels or []
    }
    return service


def test_ensure_labels_creates_only_the_missing_ones() -> None:
    service = _fake_service(
        existing_labels=[{"name": "INBOX", "id": "INBOX"}, {"name": "Review", "id": "Label_1"}]
    )
    create_call = service.users.return_value.labels.return_value.create
    create_call.return_value.execute.return_value = {"id": "Label_2", "name": "Financial"}

    client = GmailWriteClient(service=service)
    result = client.ensure_labels(["Review", "Financial"])

    assert result == {"Review": "Label_1", "Financial": "Label_2"}
    # Only the missing label was created — one call, not two.
    assert create_call.call_count == 1
    kwargs = create_call.call_args.kwargs
    assert kwargs["body"]["name"] == "Financial"


def test_ensure_labels_lists_only_once_across_calls() -> None:
    """The label listing is cached for the client's lifetime — a batch apply
    over many messages must not re-list labels before every one."""
    service = _fake_service(existing_labels=[{"name": "Review", "id": "Label_1"}])
    client = GmailWriteClient(service=service)

    client.ensure_labels(["Review"])
    client.ensure_labels(["Review"])

    list_call = service.users.return_value.labels.return_value.list
    assert list_call.call_count == 1


def test_modify_message_combines_add_and_remove_into_one_call() -> None:
    service = _fake_service()
    modify_call = service.users.return_value.messages.return_value.modify
    modify_call.return_value.execute.return_value = {"id": "m1", "labelIds": ["INBOX"]}

    client = GmailWriteClient(service=service)
    client.modify_message("m1", add_label_ids=["INBOX", "Label_1"], remove_label_ids=["Review"])

    assert modify_call.call_count == 1
    body = modify_call.call_args.kwargs["body"]
    assert set(body["addLabelIds"]) == {"INBOX", "Label_1"}
    assert body["removeLabelIds"] == ["Review"]


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


def test_ensure_labels_sets_color_on_creation() -> None:
    service = _fake_service()
    create_call = service.users.return_value.labels.return_value.create
    create_call.return_value.execute.return_value = {"id": "Label_1", "name": "Critical"}

    client = GmailWriteClient(service=service)
    client.ensure_labels(["Critical"])

    body = create_call.call_args.kwargs["body"]
    assert body["color"] == LABEL_COLORS["Critical"]


def test_ensure_labels_omits_color_for_an_unmapped_name() -> None:
    """A label name with no entry in LABEL_COLORS (shouldn't happen for the
    real taxonomy, but must not crash) creates without a color key at all."""
    service = _fake_service()
    create_call = service.users.return_value.labels.return_value.create
    create_call.return_value.execute.return_value = {"id": "Label_1", "name": "Something/Else"}

    client = GmailWriteClient(service=service)
    client.ensure_labels(["Something/Else"])

    body = create_call.call_args.kwargs["body"]
    assert "color" not in body


def test_sync_label_colors_patches_every_existing_taxonomy_label() -> None:
    existing = [
        {"name": name, "id": f"Label_{i}"} for i, name in enumerate(LABEL_COLORS)
    ]
    service = _fake_service(existing_labels=existing)
    patch_call = service.users.return_value.labels.return_value.patch
    patch_call.return_value.execute.return_value = {}

    client = GmailWriteClient(service=service)
    outcomes = client.sync_label_colors()

    assert patch_call.call_count == len(LABEL_COLORS)
    assert all(v == "colored" for v in outcomes.values())
    first_name = next(iter(LABEL_COLORS))
    matching_calls = [
        c for c in patch_call.call_args_list if c.kwargs["id"] == "Label_0"
    ]
    assert matching_calls[0].kwargs["body"]["color"] == LABEL_COLORS[first_name]


def test_sync_label_colors_skips_labels_not_yet_created() -> None:
    service = _fake_service(existing_labels=[])
    client = GmailWriteClient(service=service)

    outcomes = client.sync_label_colors()

    assert all(v == "not created yet" for v in outcomes.values())
    assert not service.users.return_value.labels.return_value.patch.called


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
