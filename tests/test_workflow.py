"""End-to-end tests that drive the real Flask app through HTTP, like a browser would.

Pinned "today" is 2026-03-10 (see conftest.py). Default settings: service 5 %, tax 10 %.
"""
from conftest import TODAY, day

ROOM = {"room_number": "101", "room_type": "Double", "capacity": "2", "rate": "100.00", "description": "Test room"}
GUEST = {"full_name": "Test Guest One", "phone": "+1 555-0101", "email": "one@example.com"}


def text(response) -> str:
    return response.get_data(as_text=True)


def add_room(post, **overrides):
    return post("/rooms/new", {**ROOM, **overrides})


def add_guest(post, **overrides):
    return post("/guests/new", {**GUEST, **overrides})


def reserve(post, guest_id, room_id, start, end, guests="1"):
    return post(
        "/reservations/new",
        {"guest_id": guest_id, "room_id": room_id, "check_in": day(start), "check_out": day(end), "num_guests": guests},
    )


def reservation_id_from(response) -> int:
    assert response.status_code == 302, text(response)
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


# ------------------------------------------------------------------ pages load
def test_every_main_page_loads(client):
    for url in ["/", "/rooms", "/rooms/new", "/guests", "/guests/new", "/reservations", "/reservations/new",
                "/availability", "/settings"]:
        response = client.get(url)
        assert response.status_code == 200, url
        assert "<title>" in text(response)


def test_unknown_records_and_pages_give_friendly_404(client):
    for url in ["/nope", "/rooms/999/edit", "/guests/999", "/reservations/999", "/reservations/999/bill"]:
        response = client.get(url)
        assert response.status_code == 404, url
        assert "not found" in text(response).lower()


# ------------------------------------------------- the main workflow, in order
def test_full_workflow_room_guest_reservation_conflict_bill_status(client, post, conn):
    # 1. add a room and a guest
    response = add_room(post, room_number="101", rate="100.00")
    assert response.status_code == 302
    response = add_guest(post)
    guest_id = response.headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms WHERE room_number = '101'").fetchone()["id"]
    assert "101" in text(client.get("/rooms"))
    assert "Test Guest One" in text(client.get("/guests"))

    # 2. create a reservation: 3 nights starting tomorrow
    res_id = reservation_id_from(reserve(post, guest_id, room_id, 1, 4))
    page = text(client.get(f"/reservations/{res_id}"))
    assert "Confirmed" in page and "Test Guest One" in page

    # 3. a conflicting booking for the same room is refused and nothing is saved
    guest2 = add_guest(post, full_name="Test Guest Two", email="two@example.com", phone="+1 555-0102")
    guest2_id = guest2.headers["Location"].rstrip("/").split("/")[-1]
    clash = reserve(post, guest2_id, room_id, 3, 6)
    assert clash.status_code == 409
    assert "already booked" in text(clash)
    assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 1
    # ...but arriving on the day the first guest leaves is fine
    assert reserve(post, guest2_id, room_id, 4, 5).status_code == 302

    # 4. the bill: 3 x 100.00 = 300.00; service 5 % = 15.00; tax 10 % of 315.00 = 31.50; total 346.50
    bill = text(client.get(f"/reservations/{res_id}/bill"))
    for expected in ["300.00", "15.00", "31.50", "346.50", "3 nights", "does not process"]:
        assert expected in bill, expected

    # 5. an extra charge changes the bill: 350.00 -> service 17.50 -> tax 36.75 -> total 404.25
    assert post(f"/reservations/{res_id}/charges", {"description": "Room service", "amount": "50.00"}).status_code == 302
    bill = text(client.get(f"/reservations/{res_id}/bill"))
    for expected in ["Room service", "350.00", "17.50", "36.75", "404.25"]:
        assert expected in bill, expected

    # 6. status: cannot check in before the arrival date
    response = post(f"/reservations/{res_id}/status", {"status": "checked_in"}, follow_redirects=True)
    assert "only available from the arrival date" in text(response)

    # 7. cancel it; the room becomes free again for those dates
    response = post(f"/reservations/{res_id}/status", {"status": "cancelled"}, follow_redirects=True)
    assert "now cancelled" in text(response)
    assert reserve(post, guest2_id, room_id, 1, 3).status_code == 302
    assert "nothing is billable" in text(client.get(f"/reservations/{res_id}/bill"))


def test_check_in_and_check_out_flow(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    res_id = reservation_id_from(reserve(post, guest_id, room_id, 0, 2))  # arrives today

    assert "now checked in" in text(post(f"/reservations/{res_id}/status", {"status": "checked_in"}, follow_redirects=True))
    # a checked-in guest cannot be "cancelled", only checked out
    response = post(f"/reservations/{res_id}/status", {"status": "cancelled"}, follow_redirects=True)
    assert "cannot be changed" in text(response)
    # extras can still be added while the guest is in the house
    assert post(f"/reservations/{res_id}/charges", {"description": "Minibar", "amount": "12.50"}).status_code == 302

    assert "now checked out" in text(post(f"/reservations/{res_id}/status", {"status": "checked_out"}, follow_redirects=True))
    # the bill is now locked and the final figure is shown
    response = post(f"/reservations/{res_id}/charges", {"description": "Late", "amount": "5"}, follow_redirects=True)
    assert "can only be added while" in text(response)
    # 2 x 100 + 12.50 = 212.50; service 10.63 (10.625 rounds half up); tax 22.31 (223.13 x 10 %)
    bill = text(client.get(f"/reservations/{res_id}/bill"))
    for expected in ["212.50", "10.63", "22.31", "245.44"]:
        assert expected in bill, expected
    # a checked-out stay no longer blocks the room
    assert reserve(post, guest_id, room_id, 0, 2).status_code == 302


# ----------------------------------------------------------------- validation
def test_reservation_form_errors_are_shown_and_nothing_is_saved(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]

    cases = [
        (reserve(post, guest_id, room_id, 3, 3), "must be after the check-in"),
        (reserve(post, guest_id, room_id, 4, 2), "must be after the check-in"),
        (reserve(post, guest_id, room_id, -1, 2), "cannot be in the past"),
        (reserve(post, guest_id, room_id, 1, 2, guests="3"), "sleeps 2"),
        (reserve(post, "", room_id, 1, 2), "choose a guest"),
        (reserve(post, guest_id, "", 1, 2), "choose a room"),
        (post("/reservations/new", {"guest_id": guest_id, "room_id": room_id, "check_in": "not-a-date",
                                    "check_out": "2026-02-30", "num_guests": "1"}), "valid date"),
    ]
    for response, message in cases:
        assert response.status_code == 400, message
        assert message in text(response), message
    assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0


def test_room_and_guest_forms_validate(client, post):
    for data, message in [
        ({"room_number": ""}, "Room number is required"),
        ({"rate": "abc"}, "Nightly rate must be a number"),
        ({"rate": "-5"}, "Nightly rate must be a number"),
        ({"rate": "0"}, "greater than zero"),
        ({"capacity": "0"}, "between 1 and 10"),
        ({"room_type": "Castle"}, "valid room type"),
    ]:
        response = add_room(post, **data)
        assert response.status_code == 400, data
        assert message in text(response), data

    for data, message in [
        ({"full_name": ""}, "Full name is required"),
        ({"phone": ""}, "Phone is required"),
        ({"phone": "12"}, "at least 7 digits"),
        ({"email": "not-an-email"}, "valid address"),
    ]:
        response = add_guest(post, **data)
        assert response.status_code == 400, data
        assert message in text(response), data


def test_duplicate_room_number_is_refused(post):
    assert add_room(post).status_code == 302
    response = add_room(post, room_number="101")
    assert response.status_code == 400 and "already exists" in text(response)


def test_edit_room_and_guest(client, post, conn):
    add_room(post)
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    response = post(f"/rooms/{room_id}/edit", {**ROOM, "rate": "175.50", "room_type": "Suite"}, follow_redirects=True)
    assert "Room updated" in text(response) and "175.50" in text(response)

    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    response = post(f"/guests/{guest_id}/edit", {**GUEST, "full_name": "Renamed Guest"}, follow_redirects=True)
    assert "Renamed Guest" in text(response)


def test_changing_room_rate_keeps_existing_bill(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    res_id = reservation_id_from(reserve(post, guest_id, room_id, 1, 2))
    post(f"/rooms/{room_id}/edit", {**ROOM, "rate": "999.00"})
    bill = text(client.get(f"/reservations/{res_id}/bill"))
    assert "115.50" in bill and "999.00" not in bill  # 100 + 5 + 10.50


def test_guest_search(client, post):
    add_guest(post, full_name="Alpha Tester", email="alpha@example.com", phone="+1 555-0111")
    add_guest(post, full_name="Beta Tester", email="beta@example.com", phone="+1 555-0222")
    page = text(client.get("/guests?q=alpha"))
    assert "Alpha Tester" in page and "Beta Tester" not in page
    assert "Beta Tester" in text(client.get("/guests?q=0222"))
    assert 'No guests match "zzz"' in text(client.get("/guests?q=zzz"))


def test_out_of_service_room_cannot_be_booked(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    post(f"/rooms/{room_id}/edit", {**ROOM})  # unchecked "in service" box => out of service
    response = reserve(post, guest_id, room_id, 1, 2)
    assert response.status_code == 400 and "out of service" in text(response)


# ------------------------------------------------------------- availability
def test_availability_api(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    reserve(post, guest_id, room_id, 2, 4)

    data = client.get(f"/api/availability?check_in={day(3)}&check_out={day(5)}&num_guests=1").get_json()
    assert data["ok"] and data["nights"] == 2
    assert data["rooms"][0]["available"] is False and "reservation" in data["rooms"][0]["reason"]

    data = client.get(f"/api/availability?check_in={day(4)}&check_out={day(6)}").get_json()
    assert data["rooms"][0]["available"] is True
    # 2 nights x 100.00 = 200.00, + 5 % service (10.00) + 10 % tax on 210.00 (21.00)
    assert data["rooms"][0]["estimate_total"] == "₹231.00"

    bad = client.get(f"/api/availability?check_in={day(5)}&check_out={day(3)}")
    assert bad.status_code == 400 and bad.get_json()["ok"] is False


def test_availability_page_grid_renders(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    reserve(post, guest_id, room_id, 1, 3)
    assert client.get("/availability").status_code == 200
    assert client.get(f"/availability?start={day(0)}&days=7").status_code == 200
    assert client.get("/availability?start=garbage&days=abc").status_code == 200  # falls back to defaults


# ----------------------------------------------------------------- settings
def test_settings_change_applies_to_new_reservations_only(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    old = reservation_id_from(reserve(post, guest_id, room_id, 1, 2))

    response = post("/settings", {"hotel_name": "Test Inn", "hotel_address": "1 Test Rd", "currency_symbol": "$",
                                  "tax_name": "VAT", "tax_percent": "20", "service_percent": "0"}, follow_redirects=True)
    assert "Settings saved" in text(response) and "Test Inn" in text(response)

    new = reservation_id_from(reserve(post, guest_id, room_id, 3, 4))
    new_bill = text(client.get(f"/reservations/{new}/bill"))
    assert "VAT (20%)" in new_bill and "$120.00" in new_bill
    old_bill = text(client.get(f"/reservations/{old}/bill"))
    assert "(10%)" in old_bill and "$115.50" in old_bill


def test_settings_validation(post):
    response = post("/settings", {"hotel_name": "X", "currency_symbol": "$", "tax_name": "Tax",
                                  "tax_percent": "150", "service_percent": "-1"})
    assert response.status_code == 400
    assert "between 0 and 100" in text(response)


# ------------------------------------------------------------------ security
def test_post_without_csrf_token_is_rejected(client, conn):
    response = client.post("/rooms/new", data=ROOM)
    assert response.status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0


def test_user_text_is_escaped_in_html(client, post):
    add_guest(post, full_name="<script>alert(1)</script>")
    page = text(client.get("/guests"))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_dashboard_reflects_data(client, post, conn):
    add_room(post)
    guest_id = add_guest(post).headers["Location"].rstrip("/").split("/")[-1]
    room_id = conn.execute("SELECT id FROM rooms").fetchone()["id"]
    reserve(post, guest_id, room_id, 0, 2)  # arrives today
    page = text(client.get("/"))
    assert "Test Guest One" in page
    assert TODAY.strftime("%d %b %Y") in page or TODAY.strftime("%a %d %b") in page
