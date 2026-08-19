"""Cross-message grouping: travel, orders, duplicates (Phase 6)."""

from __future__ import annotations

from datetime import date

from app.intelligence import duplicates, orders, travel
from tests.fixtures.emails import make_message

TODAY = date(2026, 8, 17)


# -------- travel --------


def test_flight_and_hotel_group_into_one_trip() -> None:
    flight = make_message(
        sender="itinerary@united.com",
        sender_name="United",
        subject="Your trip to Boston is booked",
        body="Flight to Boston on September 10, 2026. Confirmation ABC123.",
        message_id="a",
        thread_id="ta",
    )
    hotel = make_message(
        sender="reservations@hotels.com",
        subject="Hotel confirmed in Boston",
        body="Check-in September 10, 2026, check-out September 12, 2026, in Boston.",
        message_id="b",
        thread_id="tb",
    )
    trips = travel.group_trips([flight, hotel], TODAY)
    assert len(trips) == 1
    assert trips[0].destination == "Boston"
    assert trips[0].segment_count == 2
    assert trips[0].status == "upcoming"


def test_unrelated_trips_stay_separate() -> None:
    a = make_message(
        subject="Flight to Boston",
        body="Flight to Boston September 10, 2026.",
        message_id="a",
        thread_id="ta",
    )
    b = make_message(
        subject="Flight to Tokyo",
        body="Flight to Tokyo December 1, 2026.",
        message_id="b",
        thread_id="tb",
    )
    assert len(travel.group_trips([a, b], TODAY)) == 2


def test_non_travel_email_is_ignored() -> None:
    m = make_message(subject="Lunch", body="Want lunch?")
    assert travel.group_trips([m], TODAY) == []


# -------- orders --------


def test_order_updates_group_by_number() -> None:
    confirmed = make_message(
        sender="auto@amazon.com",
        sender_name="Amazon",
        subject="Order #90887766 confirmed",
        body="Thank you for your order.",
        message_id="a",
        thread_id="ta",
    )
    shipped = make_message(
        sender="auto@amazon.com",
        sender_name="Amazon",
        subject="Order #90887766 has shipped",
        body="Your order has shipped. Tracking number 1Z999.",
        message_id="b",
        thread_id="tb",
    )
    grouped = orders.group_orders([confirmed, shipped])
    assert len(grouped) == 1
    assert grouped[0].order_id == "90887766"
    assert grouped[0].status == "shipped"
    assert grouped[0].merchant == "Amazon"


def test_delivery_problem_is_flagged() -> None:
    m = make_message(
        sender="auto@amazon.com",
        subject="Delivery delayed for order #5544",
        body="Your delivery is delayed and action is needed.",
        message_id="a",
        thread_id="ta",
    )
    grouped = orders.group_orders([m])
    assert grouped[0].status == "problem"
    assert grouped[0].has_problem is True


def test_numberless_confirmations_do_not_merge_across_merchants() -> None:
    # "Order Confirmation" with no number must not key on the word itself.
    a = make_message(
        sender="orders@shopone.com",
        subject="Order Confirmation",
        body="Thank you for your order.",
        message_id="a",
        thread_id="ta",
    )
    b = make_message(
        sender="orders@shoptwo.com",
        subject="Order Confirmation",
        body="Thank you for your order.",
        message_id="b",
        thread_id="tb",
    )
    grouped = orders.group_orders([a, b])
    assert len(grouped) == 2
    assert all(o.order_id == "" for o in grouped)


def test_delivered_status_is_the_latest_stage() -> None:
    m = make_message(
        sender="auto@shop.com",
        subject="Order #7788 delivered",
        body="Your order has been delivered.",
        message_id="a",
        thread_id="ta",
    )
    assert orders.group_orders([m])[0].status == "delivered"


# -------- duplicates --------


def test_identical_messages_from_same_sender_are_grouped() -> None:
    a = make_message(
        sender="promo@shop.com",
        subject="Fifty percent off sale ends soon friends",
        message_id="a",
        thread_id="ta",
    )
    b = make_message(
        sender="promo@shop.com",
        subject="Fifty percent off sale ends soon friends",
        message_id="b",
        thread_id="tb",
    )
    groups = duplicates.find_duplicates([a, b])
    assert len(groups) == 1
    assert set(groups[0].message_ids) == {"a", "b"}
    assert duplicates.duplicate_message_ids(groups) == {"b"}


def test_same_text_different_senders_not_grouped() -> None:
    a = make_message(
        sender="promo@shop.com",
        subject="Welcome to our friendly weekly newsletter today",
        message_id="a",
    )
    b = make_message(
        sender="promo@other.com",
        subject="Welcome to our friendly weekly newsletter today",
        message_id="b",
    )
    assert duplicates.find_duplicates([a, b]) == []


def test_distinct_messages_not_grouped() -> None:
    a = make_message(
        sender="p@shop.com",
        subject="Your receipt from yesterday's grocery purchase",
        message_id="a",
    )
    b = make_message(
        sender="p@shop.com",
        subject="Completely different weekend travel plans ahead",
        message_id="b",
    )
    assert duplicates.find_duplicates([a, b]) == []
