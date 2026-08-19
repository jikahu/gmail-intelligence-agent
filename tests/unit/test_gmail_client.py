"""Gmail read-only client tests — mocked googleapiclient service."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.gmail.client import (
    GmailReadClient,
    format_summaries_for_display,
    get_client,
)


def _fake_service_returning(messages: list[dict], profile: dict | None = None) -> MagicMock:
    service = MagicMock()

    # users().messages().list(...).execute()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": m["id"]} for m in messages]
    }

    # users().messages().get(...).execute() — one call per stub.
    get_call = service.users.return_value.messages.return_value.get
    get_call.return_value.execute.side_effect = messages

    if profile is not None:
        service.users.return_value.getProfile.return_value.execute.return_value = profile
    return service


def test_list_recent_summaries_uses_metadata_format() -> None:
    fake_msg = {
        "id": "m1",
        "threadId": "t1",
        "snippet": "hello there",
        "labelIds": ["INBOX", "IMPORTANT"],
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "Subject", "value": "Hi"},
                {"name": "Date", "value": "Wed, 13 Aug 2026 10:00:00 -0400"},
            ]
        },
    }
    service = _fake_service_returning([fake_msg])
    client = GmailReadClient(service=service)

    summaries = client.list_recent_message_summaries(max_results=1)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.id == "m1"
    assert s.subject == "Hi"
    assert s.sender == "alice@example.com"
    assert "INBOX" in s.label_ids

    # Confirm we asked for the metadata format, not full body.
    get_call = service.users.return_value.messages.return_value.get
    kwargs = get_call.call_args.kwargs
    assert kwargs["format"] == "metadata"
    assert set(kwargs["metadataHeaders"]) == {"From", "Subject", "Date"}


def test_get_profile_delegates() -> None:
    service = _fake_service_returning([], profile={"emailAddress": "jikahu@gmail.com"})
    client = GmailReadClient(service=service)
    profile = client.get_profile()
    assert profile["emailAddress"] == "jikahu@gmail.com"


def test_format_summaries_for_display_is_json_safe() -> None:
    from app.gmail.client import MessageSummary

    out = format_summaries_for_display([
        MessageSummary(
            id="m1", thread_id="t1", sender="a@b.com",
            subject="Hi", date="Wed, 13 Aug", snippet="hey",
            label_ids=["INBOX", "IMPORTANT"],
        )
    ])
    assert out[0]["from"] == "a@b.com"
    assert out[0]["labels"] == "INBOX,IMPORTANT"


def test_get_client_raises_without_stored_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.gmail import tokens as tokens_module

    monkeypatch.setattr(tokens_module, "TOKEN_DIR", tmp_path / "oauth_tokens")
    monkeypatch.setattr(
        tokens_module, "TOKEN_FILE", tmp_path / "oauth_tokens" / "token.json.enc"
    )
    with pytest.raises(FileNotFoundError):
        get_client()


def test_get_thread_full_requests_full_format() -> None:
    service = MagicMock()
    service.users.return_value.threads.return_value.get.return_value.execute.return_value = {
        "id": "t1",
        "messages": [{"id": "m1", "threadId": "t1"}],
    }
    client = GmailReadClient(service=service)

    raw = client.get_thread_full("t1")

    assert raw["messages"][0]["id"] == "m1"
    kwargs = service.users.return_value.threads.return_value.get.call_args.kwargs
    assert kwargs["format"] == "full"
    assert kwargs["id"] == "t1"


def test_list_history_passes_through_history_types_and_page_token() -> None:
    service = MagicMock()
    service.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "history": [],
        "historyId": "150",
    }
    client = GmailReadClient(service=service)

    result = client.list_history("100", history_types=["messageAdded"], page_token="p1")

    assert result["historyId"] == "150"
    kwargs = service.users.return_value.history.return_value.list.call_args.kwargs
    assert kwargs["startHistoryId"] == "100"
    assert kwargs["historyTypes"] == ["messageAdded"]
    assert kwargs["pageToken"] == "p1"


def test_list_message_ids_passes_through_query_and_page_token() -> None:
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "m1", "threadId": "t1"}],
        "nextPageToken": "p2",
        "resultSizeEstimate": 1234,
    }
    client = GmailReadClient(service=service)

    result = client.list_message_ids(query="after:2025/01/01", max_results=100, page_token="p1")

    assert result["nextPageToken"] == "p2"
    assert result["resultSizeEstimate"] == 1234
    kwargs = service.users.return_value.messages.return_value.list.call_args.kwargs
    assert kwargs["q"] == "after:2025/01/01"
    assert kwargs["maxResults"] == 100
    assert kwargs["pageToken"] == "p1"


def test_list_message_ids_omits_query_and_page_token_when_absent() -> None:
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }
    client = GmailReadClient(service=service)

    client.list_message_ids(max_results=50)

    kwargs = service.users.return_value.messages.return_value.list.call_args.kwargs
    assert "q" not in kwargs
    assert "pageToken" not in kwargs
    assert kwargs["maxResults"] == 50


def test_client_has_no_write_methods() -> None:
    """Nothing in the read client should look like a Gmail mutation."""
    forbidden = {"send", "modify", "trash", "untrash", "delete", "batchDelete"}
    for attr in dir(GmailReadClient):
        assert not any(word in attr.lower() for word in forbidden), (
            f"GmailReadClient exposes suspicious attribute: {attr}"
        )
