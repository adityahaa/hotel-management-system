"""Web routes. Each route is thin: read input, call a service, render a template."""
from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from . import db, services
from .billing import format_money
from .clock import today
from .validation import (
    ConflictError,
    NotFoundError,
    StateError,
    ValidationError,
    parse_iso_date,
)

bp = Blueprint("main", __name__)


def _conn():
    return db.get_db()


# ================================================================= dashboard
@bp.get("/")
def dashboard():
    return render_template("dashboard.html", **services.dashboard_data(_conn(), today()))


# ===================================================================== rooms
@bp.get("/rooms")
def rooms():
    return render_template("rooms.html", states=services.room_states(_conn(), today()))


def _room_form_page(room, form, errors, status=200):
    return (
        render_template("room_form.html", room=room, form=form, errors=errors, room_types=services.ROOM_TYPES),
        status,
    )


@bp.route("/rooms/new", methods=["GET", "POST"])
def room_new():
    if request.method == "POST":
        try:
            services.create_room(_conn(), request.form)
        except ValidationError as exc:
            return _room_form_page(None, request.form, exc.errors, 400)
        flash("Room added.", "success")
        return redirect(url_for("main.rooms"))
    return _room_form_page(None, {"capacity": "2"}, {})


@bp.route("/rooms/<int:room_id>/edit", methods=["GET", "POST"])
def room_edit(room_id):
    room = services.get_room(_conn(), room_id)
    if request.method == "POST":
        try:
            services.update_room(_conn(), room_id, request.form, today())
        except ValidationError as exc:
            return _room_form_page(room, request.form, exc.errors, 400)
        flash("Room updated.", "success")
        return redirect(url_for("main.rooms"))
    return _room_form_page(room, services.room_to_form(room), {})


# ==================================================================== guests
@bp.get("/guests")
def guests():
    query = request.args.get("q", "").strip()
    return render_template("guests.html", guests=services.search_guests(_conn(), query), q=query)


def _guest_form_page(guest, form, errors, status=200):
    return render_template("guest_form.html", guest=guest, form=form, errors=errors), status


@bp.route("/guests/new", methods=["GET", "POST"])
def guest_new():
    if request.method == "POST":
        try:
            guest_id = services.create_guest(_conn(), request.form)
        except ValidationError as exc:
            return _guest_form_page(None, request.form, exc.errors, 400)
        flash("Guest added.", "success")
        return redirect(url_for("main.guest_detail", guest_id=guest_id))
    return _guest_form_page(None, {}, {})


@bp.get("/guests/<int:guest_id>")
def guest_detail(guest_id):
    conn = _conn()
    return render_template(
        "guest_detail.html",
        guest=services.get_guest(conn, guest_id),
        reservations=services.guest_reservations(conn, guest_id),
    )


@bp.route("/guests/<int:guest_id>/edit", methods=["GET", "POST"])
def guest_edit(guest_id):
    guest = services.get_guest(_conn(), guest_id)
    if request.method == "POST":
        try:
            services.update_guest(_conn(), guest_id, request.form)
        except ValidationError as exc:
            return _guest_form_page(guest, request.form, exc.errors, 400)
        flash("Guest details updated.", "success")
        return redirect(url_for("main.guest_detail", guest_id=guest_id))
    return _guest_form_page(guest, dict(guest), {})


# =============================================================== reservations
@bp.get("/reservations")
def reservations():
    status = request.args.get("status", "")
    query = request.args.get("q", "").strip()
    return render_template(
        "reservations.html",
        reservations=services.list_reservations(_conn(), status, query),
        status=status,
        q=query,
        status_labels=services.STATUS_LABELS,
    )


def _reservation_form_page(form, errors, status=200, conflict=None):
    conn = _conn()
    rows, date_errors = services.availability_for_form(conn, form, today())
    # Show date problems found while checking availability, even on a first GET.
    shown_errors = dict(errors)
    for key, message in date_errors.items():
        shown_errors.setdefault(key, message)
    return (
        render_template(
            "reservation_form.html",
            form=form,
            errors=shown_errors,
            conflict=conflict,
            guests=services.list_guests(conn),
            rooms=services.list_rooms(conn),
            availability=rows,
            today=today(),
        ),
        status,
    )


@bp.route("/reservations/new", methods=["GET", "POST"])
def reservation_new():
    if request.method == "POST":
        try:
            reservation_id = services.create_reservation(_conn(), request.form, today())
        except ValidationError as exc:
            return _reservation_form_page(request.form, exc.errors, 400)
        except ConflictError as exc:
            return _reservation_form_page(request.form, {}, 409, conflict=exc.message)
        flash("Reservation created.", "success")
        return redirect(url_for("main.reservation_detail", reservation_id=reservation_id))

    # GET: prefill from the query string; default to a one-night stay starting today.
    form = {k: v for k, v in request.args.items()}
    form.setdefault("check_in", today().isoformat())
    form.setdefault("check_out", (today() + timedelta(days=1)).isoformat())
    form.setdefault("num_guests", "1")
    return _reservation_form_page(form, {})


def _detail_page(reservation_id, form=None, errors=None, status=200):
    conn = _conn()
    data = services.get_bill(conn, reservation_id)
    res = data["reservation"]
    return (
        render_template(
            "reservation_detail.html",
            res=res,
            bill=data["bill"],
            billable=data["billable"],
            charges=data["charges"],
            actions=services.allowed_actions(res, today()),
            errors=errors or {},
            form=form or {},
        ),
        status,
    )


@bp.get("/reservations/<int:reservation_id>")
def reservation_detail(reservation_id):
    return _detail_page(reservation_id)


@bp.post("/reservations/<int:reservation_id>/status")
def reservation_status(reservation_id):
    try:
        services.change_status(_conn(), reservation_id, request.form.get("status", ""), today())
    except (StateError, ValidationError) as exc:
        flash(str(exc), "error")
    else:
        label = services.STATUS_LABELS[request.form["status"]].lower()
        flash(f"Reservation #{reservation_id} is now {label}.", "success")
    return redirect(url_for("main.reservation_detail", reservation_id=reservation_id))


@bp.post("/reservations/<int:reservation_id>/charges")
def charge_add(reservation_id):
    try:
        services.add_charge(_conn(), reservation_id, request.form)
    except ValidationError as exc:
        return _detail_page(reservation_id, request.form, exc.errors, 400)
    except StateError as exc:
        flash(str(exc), "error")
    else:
        flash("Extra charge added to the bill.", "success")
    return redirect(url_for("main.reservation_detail", reservation_id=reservation_id))


@bp.post("/reservations/<int:reservation_id>/charges/<int:charge_id>/delete")
def charge_delete(reservation_id, charge_id):
    try:
        services.remove_charge(_conn(), reservation_id, charge_id)
    except StateError as exc:
        flash(str(exc), "error")
    else:
        flash("Charge removed.", "success")
    return redirect(url_for("main.reservation_detail", reservation_id=reservation_id))


@bp.get("/reservations/<int:reservation_id>/bill")
def bill(reservation_id):
    return render_template("bill.html", today=today(), **services.get_bill(_conn(), reservation_id))


# ============================================================== availability
@bp.get("/availability")
def availability():
    start = parse_iso_date(request.args.get("start")) or today()
    days = request.args.get("days", type=int)
    if days not in (7, 14, 28):
        days = 14
    grid = services.availability_grid(_conn(), start, days)
    return render_template(
        "availability.html",
        grid=grid,
        days=days,
        today=today(),
        one_day=timedelta(days=1),
        prev_start=start - timedelta(days=days),
        next_start=start + timedelta(days=days),
    )


@bp.get("/api/availability")
def api_availability():
    """JSON used by the booking form to refresh room availability live."""
    conn = _conn()
    rows, errors = services.availability_for_form(conn, request.args, today())
    if rows is None:
        message = next(iter(errors.values()), "Choose a check-in and a check-out date.")
        return jsonify(ok=False, error=message, errors=errors), 400
    symbol = services.get_settings(conn)["currency_symbol"]
    return jsonify(
        ok=True,
        nights=rows[0]["nights"] if rows else 0,
        rooms=[
            {
                "id": item["room"]["id"],
                "room_number": item["room"]["room_number"],
                "room_type": item["room"]["room_type"],
                "capacity": item["room"]["capacity"],
                "rate": format_money(item["room"]["rate_cents"], symbol),
                "available": item["available"],
                "reason": item["reason"],
                "estimate_total": format_money(item["estimate_total_cents"], symbol),
            }
            for item in rows
        ],
    )


# ================================================================== settings
@bp.route("/settings", methods=["GET", "POST"])
def settings():
    conn = _conn()
    if request.method == "POST":
        try:
            services.update_settings(conn, request.form)
        except ValidationError as exc:
            return render_template("settings.html", form=request.form, errors=exc.errors), 400
        flash("Settings saved. They apply to new reservations; existing bills keep the rates they were booked with.", "success")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", form=services.get_settings(conn), errors={})
