"""Synthetic demo data.

Everything here is made up for demonstration: invented names, example.com
emails and phone numbers from the 555-01xx range that is reserved for fiction.
Dates are relative to today, so the dashboard always has something to show.
"""
from __future__ import annotations

from datetime import date, timedelta

from .db import transaction
from .services import get_settings

# (room number, type, capacity, rate in minor units, description)
ROOMS = [
    ("101", "Single", 1, 350000, "Ground floor, garden side."),
    ("102", "Single", 1, 350000, "Ground floor, quiet corner."),
    ("103", "Double", 2, 520000, "Queen bed, work desk."),
    ("104", "Double", 2, 520000, "Queen bed, city view."),
    ("105", "Twin", 2, 480000, "Two single beds."),
    ("201", "Double", 2, 560000, "Queen bed, balcony."),
    ("202", "Deluxe", 3, 750000, "King bed plus sofa bed."),
    ("203", "Deluxe", 3, 750000, "King bed plus sofa bed, balcony."),
    ("204", "Twin", 2, 500000, "Under maintenance in this demo."),
    ("301", "Suite", 4, 1200000, "Separate living area."),
    ("302", "Suite", 4, 1200000, "Corner suite, two bathrooms."),
]

GUESTS = [
    ("Meera Kulkarni", "meera.k@example.com", "+1 555-0101", "Demo address 1, Sample City"),
    ("Daniel Okafor", "daniel.o@example.com", "+1 555-0102", "Demo address 2, Sample City"),
    ("Sofia Lindqvist", "sofia.l@example.com", "+1 555-0103", "Demo address 3, Sample Town"),
    ("Arjun Mehta", "arjun.m@example.com", "+1 555-0104", "Demo address 4, Sample Town"),
    ("Hannah Becker", "hannah.b@example.com", "+1 555-0105", "Demo address 5, Sample City"),
    ("Luis Fernandez", "luis.f@example.com", "+1 555-0106", "Demo address 6, Sample City"),
    ("Yuki Tanaka", "yuki.t@example.com", "+1 555-0107", "Demo address 7, Sample Town"),
    ("Chloe Martin", "chloe.m@example.com", "+1 555-0108", "Demo address 8, Sample City"),
    ("Omar Haddad", "omar.h@example.com", "+1 555-0109", "Demo address 9, Sample Town"),
    ("Grace Mensah", "grace.m@example.com", "+1 555-0110", "Demo address 10, Sample City"),
]

# (guest index, room number, check-in offset, check-out offset, guests, status, extra charges)
# Offsets are days from today.
RESERVATIONS = [
    (0, "101", -5, -2, 1, "checked_out", [("Laundry", 45000)]),
    (1, "103", -1, 2, 2, "checked_in", [("Room service dinner", 125000)]),
    (2, "105", -1, 0, 2, "checked_in", []),
    (3, "202", 0, 3, 2, "confirmed", []),
    (4, "301", 2, 6, 3, "confirmed", []),
    (5, "104", 1, 3, 2, "confirmed", []),
    (6, "201", 10, 13, 2, "confirmed", []),
    (7, "102", 3, 5, 1, "cancelled", []),
    (8, "203", -10, -7, 2, "checked_out", [("Airport pickup", 80000)]),
]


def demo_data_exists(conn) -> bool:
    return bool(
        conn.execute("SELECT (SELECT COUNT(*) FROM rooms) + (SELECT COUNT(*) FROM guests)").fetchone()[0]
    )


def seed_demo(conn, today: date) -> bool:
    """Insert demo records. Does nothing (returns False) if data already exists."""
    if demo_data_exists(conn):
        return False
    settings = get_settings(conn)
    with transaction(conn):
        room_ids = {}
        for number, rtype, capacity, rate, description in ROOMS:
            active = 0 if number == "204" else 1
            cur = conn.execute(
                "INSERT INTO rooms (room_number, room_type, capacity, rate_cents, description, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (number, rtype, capacity, rate, description, active),
            )
            room_ids[number] = cur.lastrowid

        guest_ids = []
        for name, email, phone, address in GUESTS:
            cur = conn.execute(
                "INSERT INTO guests (full_name, email, phone, address, notes) VALUES (?, ?, ?, ?, ?)",
                (name, email, phone, address, "Synthetic demo record."),
            )
            guest_ids.append(cur.lastrowid)

        for guest_idx, room_no, start, end, num_guests, status, extras in RESERVATIONS:
            rate = next(r[3] for r in ROOMS if r[0] == room_no)
            stamp = (today + timedelta(days=start)).isoformat() + "T14:00:00"
            cur = conn.execute(
                "INSERT INTO reservations (guest_id, room_id, check_in, check_out, num_guests, status, rate_cents, "
                "tax_percent, service_percent, notes, checked_in_at, checked_out_at, cancelled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guest_ids[guest_idx],
                    room_ids[room_no],
                    (today + timedelta(days=start)).isoformat(),
                    (today + timedelta(days=end)).isoformat(),
                    num_guests,
                    status,
                    rate,
                    settings["tax_percent"],
                    settings["service_percent"],
                    "Demo reservation.",
                    stamp if status in ("checked_in", "checked_out") else None,
                    (today + timedelta(days=end)).isoformat() + "T11:00:00" if status == "checked_out" else None,
                    stamp if status == "cancelled" else None,
                ),
            )
            for description, amount in extras:
                conn.execute(
                    "INSERT INTO charges (reservation_id, description, amount_cents) VALUES (?, ?, ?)",
                    (cur.lastrowid, description, amount),
                )
    return True
