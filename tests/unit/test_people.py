"""People API client tests — mocked googleapiclient service."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.gmail.people import PeopleReadClient


def _service_with(connections: list[dict], other: list[dict]) -> MagicMock:
    service = MagicMock()
    service.people.return_value.connections.return_value.list.return_value.execute.return_value = {
        "connections": connections,
        "nextPageToken": None,
    }
    service.otherContacts.return_value.list.return_value.execute.return_value = {
        "otherContacts": other,
        "nextPageToken": None,
    }
    return service


def test_list_contacts_extracts_emails() -> None:
    service = _service_with(
        connections=[
            {
                "names": [{"displayName": "Alice"}],
                "emailAddresses": [{"value": "Alice@Example.com"}],
            }
        ],
        other=[],
    )
    client = PeopleReadClient(service=service)
    contacts = client.list_contacts()
    assert len(contacts) == 1
    assert contacts[0].email == "alice@example.com"
    assert contacts[0].display_name == "Alice"
    assert contacts[0].source == "contacts"


def test_list_other_contacts_extracts_emails() -> None:
    service = _service_with(
        connections=[],
        other=[
            {
                "names": [{"displayName": "Bob"}],
                "emailAddresses": [{"value": "bob@example.com"}],
            }
        ],
    )
    client = PeopleReadClient(service=service)
    other = client.list_other_contacts()
    assert len(other) == 1
    assert other[0].email == "bob@example.com"
    assert other[0].source == "other_contacts"


def test_all_known_emails_dedupes_across_lists() -> None:
    service = _service_with(
        connections=[{"emailAddresses": [{"value": "a@x.com"}, {"value": "b@x.com"}]}],
        other=[{"emailAddresses": [{"value": "B@x.com"}, {"value": "c@x.com"}]}],
    )
    client = PeopleReadClient(service=service)
    known = client.all_known_emails()
    assert known == {"a@x.com", "b@x.com", "c@x.com"}
