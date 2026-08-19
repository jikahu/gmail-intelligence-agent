"""Google People API — read-only Contacts + Other Contacts.

Used by CLAUDE.md §8 "prior correspondents / known contacts" protection. Kept
tiny in Phase 1: fetch email addresses, expose an ``is_known_contact()``
lookup. Later phases add richer profile data as needed.

Contacts and Other Contacts are two separate lists in Google's model:

* **Contacts** — people the user has explicitly added.
* **Other Contacts** — people the user has emailed/replied to but not saved
  (frequent correspondents). This is what actually powers the "prior
  correspondent" protection rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from googleapiclient.discovery import Resource

from app.google_api import build_service, load_stored_token_or_raise
from app.logging_config import get_logger

log = get_logger("app.gmail.people")

_PEOPLE_API = ("people", "v1")


@dataclass(frozen=True)
class ContactEmail:
    email: str
    display_name: str | None
    source: str  # "contacts" or "other_contacts"


def _extract_emails(
    connections: Iterable[dict], source: str
) -> list[ContactEmail]:
    out: list[ContactEmail] = []
    for person in connections or []:
        names = person.get("names") or []
        display_name = names[0].get("displayName") if names else None
        for e in person.get("emailAddresses") or []:
            value = (e.get("value") or "").strip()
            if value:
                out.append(
                    ContactEmail(
                        email=value.lower(),
                        display_name=display_name,
                        source=source,
                    )
                )
    return out


class PeopleReadClient:
    def __init__(self, service: Resource) -> None:
        self._service = service

    def list_contacts(self, page_size: int = 200, max_pages: int = 20) -> list[ContactEmail]:
        results: list[ContactEmail] = []
        page_token: str | None = None
        for _ in range(max_pages):
            resp = (
                self._service.people()
                .connections()
                .list(
                    resourceName="people/me",
                    pageSize=page_size,
                    personFields="names,emailAddresses",
                    pageToken=page_token,
                )
                .execute()
            )
            results.extend(_extract_emails(resp.get("connections") or [], "contacts"))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def list_other_contacts(self, page_size: int = 100, max_pages: int = 20) -> list[ContactEmail]:
        results: list[ContactEmail] = []
        page_token: str | None = None
        for _ in range(max_pages):
            resp = (
                self._service.otherContacts()
                .list(
                    pageSize=page_size,
                    readMask="names,emailAddresses",
                    pageToken=page_token,
                )
                .execute()
            )
            results.extend(
                _extract_emails(resp.get("otherContacts") or [], "other_contacts")
            )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def all_known_emails(self) -> set[str]:
        """Union of Contacts + Other Contacts, lowercased."""
        contacts = self.list_contacts()
        other = self.list_other_contacts()
        combined = {c.email for c in contacts} | {c.email for c in other}
        log.info(
            "people_lookup_completed",
            extra={
                "contacts_count": len(contacts),
                "other_contacts_count": len(other),
                "unique_emails": len(combined),
            },
        )
        return combined


def get_client() -> PeopleReadClient:
    stored = load_stored_token_or_raise()
    return PeopleReadClient(service=build_service(*_PEOPLE_API, stored=stored))


__all__ = ("ContactEmail", "PeopleReadClient", "get_client")
