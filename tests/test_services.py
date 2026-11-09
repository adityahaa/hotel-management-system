"""Business rules: rooms, guests, overlaps, status changes, charges, bills."""
import sqlite3

import pytest

from conftest import TODAY, day
from hotel import services
from hotel.validation import ConflictError, NotFoundError, StateError, ValidationError


# ------------------------------------------------------------------ rooms
def test_add_and_update_room(conn, make_room):
    room_id = make_room("101", "Double", "2", "120.50", "Garden view")
    room = services.get_room(conn, room_id)
    assert room["rate_cents"] == 12050 and room["capacity"] == 2 and room["is_active"] == 1

    services.update_room(
        conn, room_id,
        {"room_number": "101", "room_type": "Suite", "capacity": "4", "rate": "200", "description": "", "is_active": "1"},
        TODAY,
    )
    room = services.get_room(conn, room_id)
    assert (room["room_type"], room["capacity"], room["rate_cents"]) == ("Suite", 4, 20000)


def test_room_number_must_be_unique_ignoring_case(conn, make_room):
    make_room("A-1")
    with pytest.raises(ValidationError) as info:
        make_room("a-1")
    assert "room_number" in info.value.errors


@pytest.mark.parametrize(
    "field,value",
    [("room_number", ""), ("room_number", "bad number!"), ("room_type", "Palace"), ("capacity", "0"),
     ("capacity", "99"), ("rate", "0"), ("rate", "-5"), ("rate", "abc"), ("rate", "")],
)
def test_room_validation(conn, field, value):
    data = {"room_number": "101", "room_type": "Double", "capacity": "2", "rate": "100"}
    data[field] = value
    with pytest.raises(ValidationError) as info:
        services.create_room(conn, data)
    key = "rate" if field == "rate" else field
    assert key in info.value.errors
    assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0


def test_cannot_take_a_room_with_upcoming_bookings_out_of_service(conn, make_room, make_guest, book):
    room, guest = make_room(), make_guest()
    book(guest, room, 2, 4)
    with pytest.raises(ValidationError) as info:
        services.update_room(
            conn, room, {"room_number": "101", "room_type": "Double", "capacity": "2", "rate": "100"}, TODAY
        )
    assert "is_active" in info.value.errors
    assert services.get_room(conn, room)["is_active"] == 1


def test_room_rate_change_does_not_alter_existing_booking(conn, make_room, make_guest, book):
    room, guest = make_room(rate="100.00"), make_guest()
    res = book(guest, room, 1, 3)
    services.update_room(
        conn, room,
        {"room_number": "101", "room_type": "Double", "capacity": "2", "rate": "500.00", "is_active": "1"}, TODAY,
    )
    assert services.get_bill(conn, res)["bill"].rate_cents == 10000
    res2 = book(guest, room, 5, 6)
    assert services.get_bill(conn, res2)["bill"].rate_cents == 50000


# ----------------------------------------------------------------- guests
def test_guest_add_search_update(conn, make_guest):
    gid = make_guest("Alice Example", "+1 555-0101", "alice@example.com")
    make_guest("Bob Sample", "+1 555-0202", "bob@example.com")
    assert [g["full_name"] for g in services.search_guests(conn, "ali")] == ["Alice Example"]
    assert [g["full_name"] for g in services.search_guests(conn, "0202")] == ["Bob Sample"]
    assert [g["full_name"] for g in services.search_guests(conn, "bob@")] == ["Bob Sample"]
    assert len(services.search_guests(conn, "")) == 2
    assert services.search_guests(conn, "zzz") == []

    services.update_guest(conn, gid, {"full_name": "Alice Changed", "phone": "+1 555-0999", "email": ""})
    assert services.get_guest(conn, gid)["full_name"] == "Alice Changed"
    assert [g["full_name"] for g in services.search_guests(conn, "changed")] == ["Alice Changed"]


def test_guest_search_treats_wildcards_literally(conn, make_guest):
    make_guest("Alice Example")
    assert services.search_guests(conn, "%") == []
    assert services.search_guests(conn, "_") == []


def test_guest_validation(conn):
    with pytest.raises(ValidationError) as info:
        services.create_guest(conn, {"full_name": "", "phone": "", "email": "nope"})
    assert set(info.value.errors) == {"full_name", "phone", "email"}
    with pytest.raises(NotFoundError):
        services.get_guest(conn, 999)


# ------------------------------------------------------ booking and overlap
def test_create_reservation_snapshots_rate_and_settings(conn, make_room, make_guest, book):
    room, guest = make_room(rate="150.00"), make_guest()
    res_id = book(guest, room, 1, 4, num_guests="2")
    res = services.get_reservation(conn, res_id)
    assert res["status"] == "confirmed"
    assert (res["rate_cents"], res["nights"], res["num_guests"]) == (15000, 3, 2)
    assert (res["tax_percent"], res["service_percent"]) == ("10", "5")


@pytest.mark.parametrize(
    "start,end",
    [(3, 5), (2, 4), (4, 6), (1, 7), (3, 4), (4, 5), (2, 6)],
    ids=["identical", "overlaps-start", "overlaps-end", "contains-existing", "first-night-only", "last-night-only", "wider"],
)
def test_overlapping_dates_are_rejected(conn, make_room, make_guest, book, start, end):
    room, guest, other = make_room(), make_guest("A Guest"), make_guest("B Guest")
    book(guest, room, 3, 5)  # occupies the nights of day 3 and day 4
    # Every (start, end) pair above shares at least one night with that stay.
    with pytest.raises(ConflictError) as info:
        book(other, room, start, end)
    assert info.value.conflicts
    assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 1


def test_back_to_back_stays_are_allowed(conn, make_room, make_guest, book):
    room, a, b = make_room(), make_guest("A"), make_guest("B")
    book(a, room, 3, 5)
    book(b, room, 5, 7)  # arrives on the day the first guest leaves
    book(b, room, 1, 3)  # leaves on the day the first guest arrives
    assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 3


def test_same_dates_in_a_different_room_are_allowed(conn, make_room, make_guest, book):
    r1, r2, guest = make_room("101"), make_room("102"), make_guest()
    book(guest, r1, 3, 5)
    book(guest, r2, 3, 5)


def test_cancelled_and_checked_out_bookings_free_the_room(conn, make_room, make_guest, book):
    room, guest = make_room(), make_guest()
    first = book(guest, room, 3, 5)
    services.change_status(conn, first, "cancelled", TODAY)
    second = book(guest, room, 3, 5)  # allowed now
    assert second != first


def test_database_trigger_blocks_overlap_even_without_app_checks(conn, make_room, make_guest, book):
    room, guest = make_room(), make_guest()
    book(guest, room, 3, 5)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute(
            "INSERT INTO reservations (guest_id, room_id, check_in, check_out, num_guests, status, rate_cents, "
            "tax_percent, service_percent) VALUES (?, ?, ?, ?, 1, 'confirmed', 100, '10', '5')",
            (guest, room, day(4), day(6)),
        )


def test_failed_booking_rolls_back(conn, make_room, make_guest, book):
    room, guest = make_room(), make_guest()
    book(guest, room, 3, 5)
    with pytest.raises(ConflictError):
        book(guest, room, 4, 6)
    assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 1
    assert not conn.in_transaction


def test_find_conflicts_can_exclude_a_reservation(conn, make_room, make_guest, book):
    from datetime import date
    room, guest = make_room(), make_guest()
    res = book(guest, room, 3, 5)
    assert services.find_conflicts(conn, room, date.fromisoformat(day(3)), date.fromisoformat(day(5)))
    assert not services.find_conflicts(conn, room, date.fromisoformat(day(3)), date.fromisoformat(day(5)), exclude_id=res)


# -------------------------------------------------- booking validation
def _data(guest, room, start, end, guests="1"):
    return {"guest_id": str(guest), "room_id": str(room), "check_in": day(start), "check_out": day(end), "num_guests": guests}


def test_date_rules(conn, make_room, make_guest):
    room, guest = make_room(), make_guest()
    cases = {
        "past check-in": (_data(guest, room, -1, 2), "check_in"),
        "checkout before checkin": (_data(guest, room, 5, 3), "check_out"),
        "zero nights": (_data(guest, room, 5, 5), "check_out"),
        "too long": (_data(guest, room, 1, 200), "check_out"),
        "too far ahead": (_data(guest, room, 800, 802), "check_in"),
    }
    for label, (data, field) in cases.items():
        with pytest.raises(ValidationError) as info:
            services.create_reservation(conn, data, TODAY)
        assert field in info.value.errors, label


def test_bad_or_missing_dates(conn, make_room, make_guest):
    room, guest = make_room(), make_guest()
    for check_in, check_out in [("", ""), ("2026-02-30", day(5)), (day(1), "garbage"), ("10/03/2026", day(5))]:
        data = _data(guest, room, 1, 2)
        data["check_in"], data["check_out"] = check_in, check_out
        with pytest.raises(ValidationError):
            services.create_reservation(conn, data, TODAY)


def test_today_is_a_valid_check_in(conn, make_room, make_guest, book):
    book(make_guest(), make_room(), 0, 1)


def test_requires_existing_guest_and_room(conn, make_room, make_guest):
    room, guest = make_room(), make_guest()
    with pytest.raises(ValidationError) as info:
        services.create_reservation(conn, _data(999, room, 1, 2), TODAY)
    assert "guest_id" in info.value.errors
    with pytest.raises(ValidationError) as info:
        services.create_reservation(conn, _data(guest, 999, 1, 2), TODAY)
    assert "room_id" in info.value.errors
    data = _data(guest, room, 1, 2)
    data["guest_id"] = ""
    with pytest.raises(ValidationError) as info:
        services.create_reservation(conn, data, TODAY)
    assert info.value.errors["guest_id"] == "Please choose a guest."


def test_capacity_and_out_of_service_rooms(conn, make_room, make_guest):
    room, guest = make_room(capacity="2"), make_guest()
    with pytest.raises(ValidationError) as info:
        services.create_reservation(conn, _data(guest, room, 1, 2, guests="3"), TODAY)
    assert "num_guests" in info.value.errors
    services.update_room(
        conn, room, {"room_number": "101", "room_type": "Double", "capacity": "2", "rate": "100"}, TODAY
    )  # no is_active -> out of service
    with pytest.raises(ValidationError) as info:
        services.create_reservation(conn, _data(guest, room, 1, 2), TODAY)
    assert "room_id" in info.value.errors


# ---------------------------------------------------------- availability
def test_availability_lists_each_room_with_reason(conn, make_room, make_guest, book):
    from datetime import date
    free, taken, small = make_room("101"), make_room("102"), make_room("103", capacity="1")
    guest = make_guest()
    res = book(guest, taken, 3, 5)
    rows = {r["room"]["room_number"]: r for r in services.room_availability(
        conn, date.fromisoformat(day(4)), date.fromisoformat(day(6)), num_guests=2)}
    assert rows["101"]["available"] is True
    assert rows["102"]["available"] is False and f"#{res}" in rows["102"]["reason"]
    assert rows["103"]["available"] is False and "Sleeps 1" in rows["103"]["reason"]
    # estimate: 2 nights x 100.00 = 200; service 10; tax 21 -> 231.00
    assert rows["101"]["estimate_total_cents"] == 23100


def test_availability_back_to_back_is_free(conn, make_room, make_guest, book):
    from datetime import date
    room, guest = make_room(), make_guest()
    book(guest, room, 3, 5)
    rows = services.room_availability(conn, date.fromisoformat(day(5)), date.fromisoformat(day(7)))
    assert rows[0]["available"] is True


def test_availability_grid_marks_taken_nights(conn, make_room, make_guest, book):
    from datetime import date
    room, guest = make_room(), make_guest()
    res = book(guest, room, 1, 3)  # nights of day 1 and day 2
    grid = services.availability_grid(conn, TODAY, 7)
    cells = grid["rows"][0]["cells"]
    taken = [i for i, c in enumerate(cells) if c["reservation"] is not None]
    assert taken == [1, 2]
    assert cells[1]["reservation"]["id"] == res


# ---------------------------------------------------------- status changes
def test_status_workflow_confirm_checkin_checkout(conn, make_room, make_guest, book):
    room, guest = make_room(), make_guest()
    res = book(guest, room, 0, 2)
    services.change_status(conn, res, "checked_in", TODAY)
    row = services.get_reservation(conn, res)
    assert row["status"] == "checked_in" and row["checked_in_at"]
    services.change_status(conn, res, "checked_out", TODAY)
    row = services.get_reservation(conn, res)
    assert row["status"] == "checked_out" and row["checked_out_at"]


def test_cancel_a_confirmed_reservation(conn, make_room, make_guest, book):
    res = book(make_guest(), make_room(), 2, 4)
    services.change_status(conn, res, "cancelled", TODAY)
    row = services.get_reservation(conn, res)
    assert row["status"] == "cancelled" and row["cancelled_at"]


@pytest.mark.parametrize(
    "path,bad",
    [([], "checked_out"),                      # confirmed -> checked_out
     (["cancelled"], "checked_in"),            # cancelled -> anything
     (["cancelled"], "confirmed"),
     (["checked_in"], "cancelled"),            # cannot cancel once checked in
     (["checked_in"], "confirmed"),
     (["checked_in", "checked_out"], "checked_in"),
     (["checked_in", "checked_out"], "cancelled")],
)
def test_invalid_transitions_are_refused(conn, make_room, make_guest, book, path, bad):
    res = book(make_guest(), make_room(), 0, 2)
    for step in path:
        services.change_status(conn, res, step, TODAY)
    before = services.get_reservation(conn, res)["status"]
    with pytest.raises(StateError):
        services.change_status(conn, res, bad, TODAY)
    assert services.get_reservation(conn, res)["status"] == before


def test_cannot_check_in_before_arrival_date(conn, make_room, make_guest, book):
    res = book(make_guest(), make_room(), 3, 5)
    with pytest.raises(StateError) as info:
        services.change_status(conn, res, "checked_in", TODAY)
    assert day(3) in str(info.value)


def test_cannot_check_in_after_stay_has_ended(conn, make_room, make_guest, book):
    from datetime import timedelta
    res = book(make_guest(), make_room(), 1, 2)
    with pytest.raises(StateError):
        services.change_status(conn, res, "checked_in", TODAY + timedelta(days=2))


def test_unknown_status_and_missing_reservation(conn):
    with pytest.raises(ValidationError):
        services.change_status(conn, 1, "banana", TODAY)
    with pytest.raises(NotFoundError):
        services.change_status(conn, 999, "cancelled", TODAY)


def test_allowed_actions_follow_status(conn, make_room, make_guest, book):
    room, guest = make_room(), make_guest()
    today_res = book(guest, room, 0, 2)
    future_res = book(guest, room, 5, 6)
    acts = {a[0]: a for a in services.allowed_actions(services.get_reservation(conn, today_res), TODAY)}
    assert acts["checked_in"][2] is True and acts["cancelled"][2] is True
    acts = {a[0]: a for a in services.allowed_actions(services.get_reservation(conn, future_res), TODAY)}
    assert acts["checked_in"][2] is False and day(5) in acts["checked_in"][3]
    services.change_status(conn, today_res, "checked_in", TODAY)
    assert [a[0] for a in services.allowed_actions(services.get_reservation(conn, today_res), TODAY)] == ["checked_out"]
    services.change_status(conn, today_res, "checked_out", TODAY)
    assert services.allowed_actions(services.get_reservation(conn, today_res), TODAY) == []


# ------------------------------------------------------ charges and bill
def test_bill_from_reservation_with_extras(conn, make_room, make_guest, book):
    room, guest = make_room(rate="100.00"), make_guest()
    res = book(guest, room, 1, 4)  # 3 nights
    services.add_charge(conn, res, {"description": "Room service", "amount": "20.00"})
    data = services.get_bill(conn, res)
    bill = data["bill"]
    assert (bill.nights, bill.room_charge_cents, bill.extras_total_cents) == (3, 30000, 2000)
    assert (bill.subtotal_cents, bill.service_cents, bill.tax_cents, bill.total_cents) == (32000, 1600, 3360, 36960)
    assert data["bill_number"] == f"B-{res:05d}" and data["billable"] is True


def test_charge_validation_and_locking(conn, make_room, make_guest, book):
    res = book(make_guest(), make_room(), 0, 2)
    for bad in [{"description": "", "amount": "5"}, {"description": "x", "amount": "0"},
                {"description": "x", "amount": "-5"}, {"description": "x", "amount": "abc"}]:
        with pytest.raises(ValidationError):
            services.add_charge(conn, res, bad)
    cid = services.add_charge(conn, res, {"description": "Parking", "amount": "10"})
    services.remove_charge(conn, res, cid)
    assert services.list_charges(conn, res) == []
    cid = services.add_charge(conn, res, {"description": "Parking", "amount": "10"})
    services.change_status(conn, res, "cancelled", TODAY)
    with pytest.raises(StateError):
        services.add_charge(conn, res, {"description": "Late", "amount": "10"})
    with pytest.raises(StateError):
        services.remove_charge(conn, res, cid)
    assert services.get_bill(conn, res)["billable"] is False


def test_changing_settings_does_not_change_existing_bill(conn, make_room, make_guest, book):
    res = book(make_guest(), make_room(rate="100.00"), 1, 3)
    before = services.get_bill(conn, res)["bill"].total_cents
    services.update_settings(conn, {
        "hotel_name": "Other Hotel", "hotel_address": "", "currency_symbol": "$", "tax_name": "VAT",
        "tax_percent": "20", "service_percent": "0"})
    assert services.get_bill(conn, res)["bill"].total_cents == before
    new_res = book(make_guest("Another"), make_room("102", rate="100.00"), 1, 3)
    # 200.00 + 0 service + 20% tax = 240.00
    assert services.get_bill(conn, new_res)["bill"].total_cents == 24000


def test_settings_validation(conn):
    good = {"hotel_name": "H", "hotel_address": "", "currency_symbol": "$", "tax_name": "Tax",
            "tax_percent": "10", "service_percent": "5"}
    for field, value in [("tax_percent", "150"), ("service_percent", "-1"), ("tax_percent", ""),
                         ("hotel_name", ""), ("currency_symbol", ""), ("tax_percent", "abc")]:
        data = dict(good, **{field: value})
        with pytest.raises(ValidationError) as info:
            services.update_settings(conn, data)
        assert field in info.value.errors
    assert services.get_settings(conn)["tax_percent"] == "10"


# -------------------------------------------------------------- dashboard
def test_dashboard_counts(conn, make_room, make_guest, book):
    r1, r2, r3 = make_room("101"), make_room("102"), make_room("103")
    guest = make_guest()
    a = book(guest, r1, 0, 2)
    services.change_status(conn, a, "checked_in", TODAY)
    book(guest, r2, 0, 1)            # reserved, arriving today
    cancelled = book(guest, r3, 3, 4)
    services.change_status(conn, cancelled, "cancelled", TODAY)
    d = services.dashboard_data(conn, TODAY)
    assert d["counts"] == {"occupied": 1, "reserved": 1, "available": 1, "closed": 0}
    assert d["active_reservations"] == 2 and d["total_reservations"] == 3
    assert d["status_counts"]["cancelled"] == 1
    assert len(d["arrivals"]) == 1 and len(d["in_house"]) == 1 and d["guest_count"] == 1
