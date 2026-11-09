"""Application logic: rooms, guests, reservations, billing data and dashboard figures.

Routes stay thin and call these functions. Every function takes an open SQLite
connection, so they can be tested without starting the web server.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .billing import compute_bill, format_cents, format_money, format_percent, nights_between
from .db import DEFAULT_SETTINGS, transaction
from .validation import (
    ROOM_NUMBER_RE,
    ConflictError,
    FieldReader,
    NotFoundError,
    StateError,
    ValidationError,
)

ROOM_TYPES = ["Single", "Double", "Twin", "Deluxe", "Suite"]
ACTIVE_STATUSES = ("confirmed", "checked_in")
STATUS_LABELS = {
    "confirmed": "Confirmed",
    "checked_in": "Checked in",
    "checked_out": "Checked out",
    "cancelled": "Cancelled",
}
# Which status changes are allowed from each status.
TRANSITIONS = {
    "confirmed": {"checked_in", "cancelled"},
    "checked_in": {"checked_out"},
    "checked_out": set(),
    "cancelled": set(),
}
MAX_STAY_NIGHTS = 90
MAX_ADVANCE_DAYS = 730

# Reservation rows joined with guest and room details; nights computed in SQL.
RES_SELECT = """
    SELECT r.*,
           g.full_name AS guest_name, g.email AS guest_email, g.phone AS guest_phone,
           rm.room_number, rm.room_type,
           CAST(julianday(r.check_out) - julianday(r.check_in) AS INTEGER) AS nights
      FROM reservations r
      JOIN guests g  ON g.id  = r.guest_id
      JOIN rooms  rm ON rm.id = r.room_id
"""
ROOM_ORDER = "ORDER BY LENGTH(room_number), room_number COLLATE NOCASE"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _like(term: str) -> str:
    """Escape LIKE wildcards in user input (used with ESCAPE '\\')."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ================================================================== settings
def get_settings(conn) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    settings.update({row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")})
    return settings


def update_settings(conn, data) -> None:
    r = FieldReader(data)
    hotel_name = r.text("hotel_name", "Hotel name", max_len=60, required=True)
    hotel_address = r.text("hotel_address", "Address", max_len=120)
    currency = r.text("currency_symbol", "Currency symbol", max_len=4, required=True)
    tax_name = r.text("tax_name", "Tax label", max_len=20, required=True)
    tax = r.percent("tax_percent", "Tax rate")
    service = r.percent("service_percent", "Service charge")
    r.check()
    new_values = {
        "hotel_name": hotel_name,
        "hotel_address": hotel_address,
        "currency_symbol": currency,
        "tax_name": tax_name,
        "tax_percent": format_percent(tax),
        "service_percent": format_percent(service),
    }
    with transaction(conn):
        for key, value in new_values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


# ===================================================================== rooms
def list_rooms(conn) -> list:
    return conn.execute(f"SELECT * FROM rooms {ROOM_ORDER}").fetchall()


def get_room(conn, room_id: int):
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if room is None:
        raise NotFoundError("Room not found.")
    return room


def room_to_form(room) -> dict:
    return {
        "room_number": room["room_number"],
        "room_type": room["room_type"],
        "capacity": str(room["capacity"]),
        "rate": format_cents(room["rate_cents"]),
        "description": room["description"],
        "is_active": "1" if room["is_active"] else "",
    }


def _read_room_form(data):
    r = FieldReader(data)
    number = r.text("room_number", "Room number", max_len=10, required=True)
    if number and not ROOM_NUMBER_RE.match(number):
        r.error("room_number", "Room number may only contain letters, numbers and hyphens (e.g. 101 or A-12).")
    room_type = r.choice("room_type", "Room type", ROOM_TYPES)
    capacity = r.integer("capacity", "Capacity", min_value=1, max_value=10)
    rate_cents = r.money("rate", "Nightly rate", min_cents=1)
    description = r.text("description", "Description", max_len=300, multiline=True)
    r.check()
    return number, room_type, capacity, rate_cents, description


def create_room(conn, data) -> int:
    number, room_type, capacity, rate_cents, description = _read_room_form(data)
    with transaction(conn):
        if conn.execute("SELECT 1 FROM rooms WHERE room_number = ? COLLATE NOCASE", (number,)).fetchone():
            raise ValidationError({"room_number": f"Room {number} already exists."})
        cur = conn.execute(
            "INSERT INTO rooms (room_number, room_type, capacity, rate_cents, description) VALUES (?, ?, ?, ?, ?)",
            (number, room_type, capacity, rate_cents, description),
        )
    return cur.lastrowid


def update_room(conn, room_id: int, data, today: date) -> None:
    number, room_type, capacity, rate_cents, description = _read_room_form(data)
    is_active = 1 if data.get("is_active") else 0
    with transaction(conn):
        get_room(conn, room_id)
        clash = conn.execute(
            "SELECT 1 FROM rooms WHERE room_number = ? COLLATE NOCASE AND id != ?", (number, room_id)
        ).fetchone()
        if clash:
            raise ValidationError({"room_number": f"Room {number} already exists."})
        if not is_active:
            pending = conn.execute(
                "SELECT COUNT(*) FROM reservations WHERE room_id = ? AND status IN ('confirmed', 'checked_in') "
                "AND check_out > ?",
                (room_id, today.isoformat()),
            ).fetchone()[0]
            if pending:
                raise ValidationError(
                    {
                        "is_active": f"This room has {pending} current or upcoming reservation(s). "
                        "Cancel or finish them before taking the room out of service."
                    }
                )
        conn.execute(
            "UPDATE rooms SET room_number = ?, room_type = ?, capacity = ?, rate_cents = ?, description = ?, "
            "is_active = ?, updated_at = ? WHERE id = ?",
            (number, room_type, capacity, rate_cents, description, is_active, _now(), room_id),
        )


# ==================================================================== guests
def _read_guest_form(data):
    r = FieldReader(data)
    full_name = r.text("full_name", "Full name", max_len=80, required=True)
    phone = r.phone("phone", "Phone", required=True)
    email = r.email("email")
    address = r.text("address", "Address", max_len=200)
    notes = r.text("notes", "Notes", max_len=300, multiline=True)
    r.check()
    return full_name, email, phone, address, notes


def create_guest(conn, data) -> int:
    values = _read_guest_form(data)
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO guests (full_name, email, phone, address, notes) VALUES (?, ?, ?, ?, ?)", values
        )
    return cur.lastrowid


def update_guest(conn, guest_id: int, data) -> None:
    full_name, email, phone, address, notes = _read_guest_form(data)
    with transaction(conn):
        get_guest(conn, guest_id)
        conn.execute(
            "UPDATE guests SET full_name = ?, email = ?, phone = ?, address = ?, notes = ?, updated_at = ? "
            "WHERE id = ?",
            (full_name, email, phone, address, notes, _now(), guest_id),
        )


def get_guest(conn, guest_id: int):
    guest = conn.execute("SELECT * FROM guests WHERE id = ?", (guest_id,)).fetchone()
    if guest is None:
        raise NotFoundError("Guest not found.")
    return guest


def list_guests(conn) -> list:
    return conn.execute("SELECT * FROM guests ORDER BY full_name COLLATE NOCASE").fetchall()


def search_guests(conn, query: str = "") -> list:
    """Guests with their booking counts; `query` matches name, email or phone."""
    query = (query or "").strip()
    sql = (
        "SELECT g.*, (SELECT COUNT(*) FROM reservations r WHERE r.guest_id = g.id) AS booking_count FROM guests g"
    )
    params: tuple = ()
    if query:
        like = _like(query)
        sql += " WHERE g.full_name LIKE ? ESCAPE '\\' OR g.email LIKE ? ESCAPE '\\' OR g.phone LIKE ? ESCAPE '\\'"
        params = (like, like, like)
    sql += " ORDER BY g.full_name COLLATE NOCASE LIMIT 500"
    return conn.execute(sql, params).fetchall()


def guest_reservations(conn, guest_id: int) -> list:
    return conn.execute(RES_SELECT + " WHERE r.guest_id = ? ORDER BY r.check_in DESC, r.id DESC", (guest_id,)).fetchall()


# ============================================================= availability
def stay_errors(check_in: date | None, check_out: date | None, today: date) -> dict:
    """Date rules shared by the booking form and the availability API."""
    errors = {}
    if check_in and check_in < today:
        errors["check_in"] = "Check-in date cannot be in the past."
    elif check_in and check_in > today + timedelta(days=MAX_ADVANCE_DAYS):
        errors["check_in"] = "Check-in date is too far ahead (the limit is two years)."
    if check_in and check_out:
        if check_out <= check_in:
            errors["check_out"] = "Check-out date must be after the check-in date."
        elif nights_between(check_in, check_out) > MAX_STAY_NIGHTS:
            errors["check_out"] = f"Stays are limited to {MAX_STAY_NIGHTS} nights."
    return errors


def find_conflicts(conn, room_id: int, check_in: date, check_out: date, exclude_id: int | None = None) -> list:
    """Active bookings for the room that overlap [check_in, check_out).

    Two stays overlap when each starts before the other ends. A guest leaving
    on the 5th and another arriving on the 5th do NOT overlap.
    """
    sql = (
        RES_SELECT + " WHERE r.room_id = ? AND r.status IN ('confirmed', 'checked_in') "
        "AND r.check_in < ? AND r.check_out > ?"
    )
    params = [room_id, check_out.isoformat(), check_in.isoformat()]
    if exclude_id is not None:
        sql += " AND r.id != ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchall()


def room_availability(conn, check_in: date, check_out: date, num_guests: int | None = None) -> list:
    """For every room: is it free for the dates, and if not, why not?"""
    settings = get_settings(conn)
    nights = nights_between(check_in, check_out)
    booked: dict = {}
    for row in conn.execute(
        RES_SELECT + " WHERE r.status IN ('confirmed', 'checked_in') AND r.check_in < ? AND r.check_out > ?",
        (check_out.isoformat(), check_in.isoformat()),
    ):
        booked.setdefault(row["room_id"], []).append(row)

    result = []
    for room in list_rooms(conn):
        reason = None
        if not room["is_active"]:
            reason = "Out of service"
        elif room["id"] in booked:
            b = booked[room["id"]][0]
            reason = f"Booked {b['check_in']} to {b['check_out']} (reservation #{b['id']})"
        elif num_guests and num_guests > room["capacity"]:
            reason = f"Sleeps {room['capacity']} at most"
        estimate = compute_bill(
            room["rate_cents"], nights, Decimal(settings["tax_percent"]), Decimal(settings["service_percent"])
        )
        result.append(
            {
                "room": room,
                "available": reason is None,
                "reason": reason,
                "nights": nights,
                "estimate_total_cents": estimate.total_cents,
            }
        )
    return result


def availability_for_form(conn, form, today: date):
    """Availability rows for the dates in a form, or (None, errors) if the dates are not usable."""
    r = FieldReader(form)
    check_in = r.date("check_in", "Check-in date")
    check_out = r.date("check_out", "Check-out date")
    r.add_errors(stay_errors(check_in, check_out, today))
    if r.errors:
        return None, r.errors
    guests = r.integer("num_guests", "Number of guests", required=False, min_value=1, max_value=10)
    return room_availability(conn, check_in, check_out, guests), {}


def availability_grid(conn, start: date, days: int = 14) -> dict:
    """Rooms x dates grid showing which nights are taken."""
    end = start + timedelta(days=days)
    dates = [start + timedelta(days=i) for i in range(days)]
    taken: dict = {}
    for res in conn.execute(
        RES_SELECT + " WHERE r.status IN ('confirmed', 'checked_in') AND r.check_in < ? AND r.check_out > ?",
        (end.isoformat(), start.isoformat()),
    ):
        day = max(date.fromisoformat(res["check_in"]), start)
        last = min(date.fromisoformat(res["check_out"]), end)
        while day < last:
            taken[(res["room_id"], day)] = res
            day += timedelta(days=1)
    rows = []
    for room in list_rooms(conn):
        cells = [
            {"date": d, "reservation": taken.get((room["id"], d)), "closed": not room["is_active"]} for d in dates
        ]
        rows.append({"room": room, "cells": cells})
    return {"dates": dates, "rows": rows, "start": start, "end": end}


# ============================================================== reservations
def get_reservation(conn, reservation_id: int):
    res = conn.execute(RES_SELECT + " WHERE r.id = ?", (reservation_id,)).fetchone()
    if res is None:
        raise NotFoundError("Reservation not found.")
    return res


def list_reservations(conn, status: str = "", query: str = "") -> list:
    sql = RES_SELECT + " WHERE 1 = 1"
    params: list = []
    if status == "active":
        sql += " AND r.status IN ('confirmed', 'checked_in')"
    elif status in STATUS_LABELS:
        sql += " AND r.status = ?"
        params.append(status)
    query = (query or "").strip()
    if query:
        like = _like(query)
        sql += " AND (g.full_name LIKE ? ESCAPE '\\' OR rm.room_number LIKE ? ESCAPE '\\' OR CAST(r.id AS TEXT) = ?)"
        params += [like, like, query.lstrip("#")]
    sql += " ORDER BY r.check_in DESC, r.id DESC LIMIT 500"
    return conn.execute(sql, params).fetchall()


def create_reservation(conn, data, today: date) -> int:
    r = FieldReader(data)
    guest_id = r.integer("guest_id", "Guest", required_message="Please choose a guest.")
    room_id = r.integer("room_id", "Room", required_message="Please choose a room.")
    check_in = r.date("check_in", "Check-in date")
    check_out = r.date("check_out", "Check-out date")
    r.add_errors(stay_errors(check_in, check_out, today))
    num_guests = r.integer("num_guests", "Number of guests", min_value=1, max_value=10)
    notes = r.text("notes", "Notes", max_len=300, multiline=True)
    r.check()

    with transaction(conn):
        if conn.execute("SELECT 1 FROM guests WHERE id = ?", (guest_id,)).fetchone() is None:
            raise ValidationError({"guest_id": "Please choose an existing guest."})
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if room is None:
            raise ValidationError({"room_id": "Please choose an existing room."})
        if not room["is_active"]:
            raise ValidationError({"room_id": f"Room {room['room_number']} is out of service."})
        if num_guests > room["capacity"]:
            raise ValidationError(
                {"num_guests": f"Room {room['room_number']} sleeps {room['capacity']} guest(s) at most."}
            )
        conflicts = find_conflicts(conn, room_id, check_in, check_out)
        if conflicts:
            first = conflicts[0]
            raise ConflictError(
                f"Room {room['room_number']} is already booked for part of this period "
                f"(reservation #{first['id']}, {first['check_in']} to {first['check_out']}). "
                "Choose different dates or another room.",
                conflicts,
            )
        settings = get_settings(conn)
        cur = conn.execute(
            "INSERT INTO reservations (guest_id, room_id, check_in, check_out, num_guests, status, rate_cents, "
            "tax_percent, service_percent, notes) VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?)",
            (
                guest_id,
                room_id,
                check_in.isoformat(),
                check_out.isoformat(),
                num_guests,
                room["rate_cents"],
                settings["tax_percent"],
                settings["service_percent"],
                notes,
            ),
        )
    return cur.lastrowid


_STATUS_TIMESTAMP_COLUMN = {
    "checked_in": "checked_in_at",
    "checked_out": "checked_out_at",
    "cancelled": "cancelled_at",
}


def change_status(conn, reservation_id: int, new_status: str, today: date) -> None:
    if new_status not in STATUS_LABELS:
        raise ValidationError({"status": "Unknown status."})
    with transaction(conn):
        res = conn.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        if res is None:
            raise NotFoundError("Reservation not found.")
        current = res["status"]
        if new_status not in TRANSITIONS[current]:
            raise StateError(
                f"A {STATUS_LABELS[current].lower()} reservation cannot be changed to "
                f"{STATUS_LABELS[new_status].lower()}."
            )
        if new_status == "checked_in":
            arrival = date.fromisoformat(res["check_in"])
            departure = date.fromisoformat(res["check_out"])
            if today < arrival:
                raise StateError(f"Check-in is only available from the arrival date ({arrival.isoformat()}).")
            if today >= departure:
                raise StateError("The stay period has already ended. Cancel this reservation instead.")
        column = _STATUS_TIMESTAMP_COLUMN[new_status]
        conn.execute(
            f"UPDATE reservations SET status = ?, {column} = ?, updated_at = ? WHERE id = ?",
            (new_status, _now(), _now(), reservation_id),
        )


def allowed_actions(res, today: date) -> list:
    """Buttons to show for a reservation: (new_status, label, enabled, hint)."""
    arrival = date.fromisoformat(res["check_in"])
    departure = date.fromisoformat(res["check_out"])
    actions = []
    if res["status"] == "confirmed":
        if arrival <= today < departure:
            actions.append(("checked_in", "Check in", True, ""))
        else:
            hint = f"Available from {arrival.isoformat()}" if today < arrival else "The stay period has ended"
            actions.append(("checked_in", "Check in", False, hint))
        actions.append(("cancelled", "Cancel reservation", True, ""))
    elif res["status"] == "checked_in":
        actions.append(("checked_out", "Check out", True, ""))
    return actions


# ================================================================== charges
def list_charges(conn, reservation_id: int) -> list:
    return conn.execute(
        "SELECT * FROM charges WHERE reservation_id = ? ORDER BY id", (reservation_id,)
    ).fetchall()


def add_charge(conn, reservation_id: int, data) -> int:
    r = FieldReader(data)
    description = r.text("description", "Description", max_len=80, required=True)
    amount = r.money("amount", "Amount", min_cents=1)
    r.check()
    with transaction(conn):
        res = conn.execute("SELECT status FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        if res is None:
            raise NotFoundError("Reservation not found.")
        if res["status"] not in ACTIVE_STATUSES:
            raise StateError("Extra charges can only be added while a reservation is confirmed or checked in.")
        cur = conn.execute(
            "INSERT INTO charges (reservation_id, description, amount_cents) VALUES (?, ?, ?)",
            (reservation_id, description, amount),
        )
    return cur.lastrowid


def remove_charge(conn, reservation_id: int, charge_id: int) -> None:
    with transaction(conn):
        res = conn.execute("SELECT status FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        if res is None:
            raise NotFoundError("Reservation not found.")
        if res["status"] not in ACTIVE_STATUSES:
            raise StateError("Charges can no longer be changed once a reservation is checked out or cancelled.")
        cur = conn.execute("DELETE FROM charges WHERE id = ? AND reservation_id = ?", (charge_id, reservation_id))
        if cur.rowcount == 0:
            raise NotFoundError("Charge not found.")


# ===================================================================== bill
def get_bill(conn, reservation_id: int) -> dict:
    res = get_reservation(conn, reservation_id)
    charges = list_charges(conn, reservation_id)
    bill = compute_bill(
        res["rate_cents"],
        res["nights"],
        Decimal(res["tax_percent"]),
        Decimal(res["service_percent"]),
        [(c["description"], c["amount_cents"]) for c in charges],
    )
    return {
        "reservation": res,
        "charges": charges,
        "bill": bill,
        "bill_number": f"B-{res['id']:05d}",
        "billable": res["status"] != "cancelled",
        "settings": get_settings(conn),
    }


# ================================================================ dashboard
def room_states(conn, today: date) -> list:
    """State of each room right now: occupied / reserved / available / closed."""
    iso = today.isoformat()
    occupied = {
        row["room_id"]: row
        for row in conn.execute(RES_SELECT + " WHERE r.status = 'checked_in'")
    }
    reserved = {
        row["room_id"]: row
        for row in conn.execute(
            RES_SELECT + " WHERE r.status = 'confirmed' AND r.check_in <= ? AND r.check_out > ?", (iso, iso)
        )
    }
    states = []
    for room in list_rooms(conn):
        if not room["is_active"]:
            state, res = "closed", None
        elif room["id"] in occupied:
            state, res = "occupied", occupied[room["id"]]
        elif room["id"] in reserved:
            state, res = "reserved", reserved[room["id"]]
        else:
            state, res = "available", None
        states.append({"room": room, "state": state, "reservation": res})
    return states


def dashboard_data(conn, today: date) -> dict:
    iso = today.isoformat()
    states = room_states(conn, today)
    counts = {s: 0 for s in ("occupied", "reserved", "available", "closed")}
    for item in states:
        counts[item["state"]] += 1

    status_counts = {s: 0 for s in STATUS_LABELS}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM reservations GROUP BY status"):
        status_counts[row["status"]] = row["n"]

    week_end = (today + timedelta(days=7)).isoformat()
    return {
        "today": today,
        "states": states,
        "counts": counts,
        "rooms_in_service": len(states) - counts["closed"],
        "status_counts": status_counts,
        "active_reservations": status_counts["confirmed"] + status_counts["checked_in"],
        "total_reservations": sum(status_counts.values()),
        "guest_count": conn.execute("SELECT COUNT(*) FROM guests").fetchone()[0],
        "arrivals": conn.execute(
            RES_SELECT + " WHERE r.status = 'confirmed' AND r.check_in = ? ORDER BY rm.room_number", (iso,)
        ).fetchall(),
        "departures": conn.execute(
            RES_SELECT + " WHERE r.status = 'checked_in' AND r.check_out <= ? ORDER BY r.check_out, rm.room_number",
            (iso,),
        ).fetchall(),
        "in_house": conn.execute(
            RES_SELECT + " WHERE r.status = 'checked_in' ORDER BY r.check_out, rm.room_number"
        ).fetchall(),
        "upcoming": conn.execute(
            RES_SELECT + " WHERE r.status = 'confirmed' AND r.check_in > ? AND r.check_in <= ? "
            "ORDER BY r.check_in, rm.room_number",
            (iso, week_end),
        ).fetchall(),
        "recent": conn.execute(RES_SELECT + " ORDER BY r.id DESC LIMIT 5").fetchall(),
    }


def money_for_json(cents: int, settings: dict) -> str:
    return format_money(cents, settings["currency_symbol"])
