"""Gmail parsing and the normalized email model."""

from __future__ import annotations

from app.classification.message import (
    domain_of,
    from_gmail,
    from_gmail_thread,
    registrable_domain,
    split_address,
    strip_html,
)
from tests.fixtures.emails import DEFAULT_USER, gmail_message


# --------------------------------------------------------------------
# Address helpers
# --------------------------------------------------------------------


def test_split_address_handles_display_names() -> None:
    assert split_address("Alice Smith <Alice@Example.COM>") == (
        "Alice Smith",
        "alice@example.com",
    )
    assert split_address("bare@example.com") == ("", "bare@example.com")
    assert split_address("") == ("", "")


def test_domain_of() -> None:
    assert domain_of("a@Example.com") == "example.com"
    assert domain_of("nonsense") == ""
    assert domain_of("") == ""


def test_registrable_domain_narrows_subdomains() -> None:
    assert registrable_domain("alerts.chase.com") == "chase.com"
    assert registrable_domain("chase.com") == "chase.com"
    assert registrable_domain("localhost") == "localhost"


def test_strip_html_removes_scripts_and_tags() -> None:
    html = "<html><style>p{color:red}</style><p>Hello <b>there</b></p><script>evil()</script></html>"
    text = strip_html(html)

    assert "Hello there" in text
    assert "evil" not in text
    assert "color:red" not in text
    assert "<" not in text


def test_strip_html_decodes_common_entities() -> None:
    assert strip_html("<p>Tom &amp; Jerry &quot;quoted&quot;</p>") == 'Tom & Jerry "quoted"'


# --------------------------------------------------------------------
# Gmail message parsing
# --------------------------------------------------------------------


def test_parses_headers_and_sender() -> None:
    raw = gmail_message(
        headers={
            "From": "Chase Alerts <alerts@chase.com>",
            "To": DEFAULT_USER,
            "Subject": "Your statement is ready",
            "Date": "Mon, 17 Aug 2026 09:30:00 -0400",
        },
        plain_body="Your August statement is available.",
    )
    message = from_gmail(raw, user_email=DEFAULT_USER)

    assert message.sender_email == "alerts@chase.com"
    assert message.sender_name == "Chase Alerts"
    assert message.sender_domain == "chase.com"
    assert message.subject == "Your statement is ready"
    assert message.to == [DEFAULT_USER]
    assert "August statement" in message.body_text
    assert message.date is not None
    assert message.date.year == 2026


def test_prefers_plain_text_over_html() -> None:
    raw = gmail_message(
        headers={"From": "a@b.com"},
        plain_body="the plain version",
        html_body="<p>the html version</p>",
    )
    message = from_gmail(raw)

    assert message.body_text == "the plain version"


def test_falls_back_to_stripped_html() -> None:
    raw = gmail_message(headers={"From": "a@b.com"}, html_body="<p>only <i>html</i></p>")
    message = from_gmail(raw)

    assert message.body_text == "only html"


def test_detects_attachments() -> None:
    raw = gmail_message(
        headers={"From": "a@b.com"},
        plain_body="see attached",
        attachments=[("statement.pdf", "application/pdf")],
    )
    message = from_gmail(raw)

    assert message.has_attachments
    assert message.attachments[0].filename == "statement.pdf"
    assert message.attachments[0].mime_type == "application/pdf"
    assert message.attachments[0].size_bytes == 2048


def test_missing_body_is_empty_not_an_error() -> None:
    """Metadata-format fetches carry no body at all."""
    raw = gmail_message(headers={"From": "a@b.com", "Subject": "Hi"})
    message = from_gmail(raw)

    assert message.body_text == ""
    assert message.subject == "Hi"


def test_malformed_base64_does_not_raise() -> None:
    raw = gmail_message(headers={"From": "a@b.com"})
    raw["payload"]["parts"] = [
        {"mimeType": "text/plain", "body": {"data": "!!!not base64!!!"}, "filename": ""}
    ]
    message = from_gmail(raw)

    assert message.body_text == ""


def test_unparseable_date_is_none() -> None:
    raw = gmail_message(headers={"From": "a@b.com", "Date": "not a date"})
    assert from_gmail(raw).date is None


def test_sent_by_user_detected_from_sender_and_label() -> None:
    by_address = from_gmail(
        gmail_message(headers={"From": DEFAULT_USER}), user_email=DEFAULT_USER
    )
    assert by_address.sent_by_user

    by_label = from_gmail(
        gmail_message(headers={"From": "other@example.com"}, labels=["SENT"]),
        user_email=DEFAULT_USER,
    )
    assert by_label.sent_by_user


def test_header_lookup_is_case_insensitive() -> None:
    message = from_gmail(
        gmail_message(headers={"List-Unsubscribe": "<mailto:x@y.com>", "From": "a@b.com"})
    )

    assert message.has_header("list-unsubscribe")
    assert message.has_header("LIST-UNSUBSCRIBE")
    assert message.header("List-Unsubscribe") == "<mailto:x@y.com>"
    assert not message.has_header("list-id")


def test_multiple_recipients_are_split() -> None:
    message = from_gmail(
        gmail_message(
            headers={
                "From": "a@b.com",
                "To": "One <one@example.com>, two@example.com",
                "Cc": "three@example.com",
            }
        )
    )

    assert message.to == ["one@example.com", "two@example.com"]
    assert message.cc == ["three@example.com"]


def test_safe_reference_never_includes_the_body() -> None:
    message = from_gmail(
        gmail_message(
            headers={"From": "a@b.com", "Subject": "Hello"},
            plain_body="SECRET ACCOUNT NUMBER 12345678",
        )
    )
    reference = message.safe_reference()

    assert "a@b.com" in reference
    assert "Hello" in reference
    assert "SECRET" not in reference


# --------------------------------------------------------------------
# Thread parsing
# --------------------------------------------------------------------


def test_thread_marks_user_participation_on_every_message() -> None:
    thread = {
        "id": "t1",
        "messages": [
            gmail_message(message_id="m1", headers={"From": "other@example.com"}),
            gmail_message(message_id="m2", headers={"From": DEFAULT_USER}),
            gmail_message(message_id="m3", headers={"From": "other@example.com"}),
        ],
    }
    messages = from_gmail_thread(thread, user_email=DEFAULT_USER)

    assert len(messages) == 3
    assert all(m.user_in_thread for m in messages)
    assert all(m.thread_message_count == 3 for m in messages)
    # The incoming messages are an active conversation, so they're protected.
    assert messages[0].is_active_thread
    assert messages[2].is_active_thread


def test_thread_without_user_is_not_an_active_conversation() -> None:
    thread = {
        "id": "t1",
        "messages": [
            gmail_message(message_id="m1", headers={"From": "a@example.com"}),
            gmail_message(message_id="m2", headers={"From": "b@example.com"}),
        ],
    }
    messages = from_gmail_thread(thread, user_email=DEFAULT_USER)

    assert not any(m.user_in_thread for m in messages)
    assert not any(m.is_active_thread for m in messages)


def test_single_message_thread_is_not_active() -> None:
    thread = {"id": "t1", "messages": [gmail_message(headers={"From": DEFAULT_USER})]}
    messages = from_gmail_thread(thread, user_email=DEFAULT_USER)

    assert not messages[0].is_active_thread
