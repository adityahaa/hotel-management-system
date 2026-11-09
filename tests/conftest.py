"""Shared test fixtures. Every test gets its own empty database and a pinned "today"."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotel import create_app, db, services  # noqa: E402

TODAY = date(2026, 3, 10)


def day(offset: int) -> str:
    """ISO date string `offset` days from the pinned today."""
    return (TODAY + timedelta(days=offset)).isoformat()


@pytest.fixture
def app(tmp_path):
    return create_app(
        {"TESTING": True, "DATABASE": str(tmp_path / "test.db"), "SECRET_KEY": "test-key", "TODAY": TODAY}
    )


@pytest.fixture
def conn(app):
    connection = db.connect(app.config["DATABASE"])
    yield connection
    connection.close()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def post(client):
    """POST helper that attaches a valid CSRF token, like a real form would."""

    def _post(url, data=None, follow_redirects=False):
        with client.session_transaction() as session:
            session["_csrf"] = "test-token"
        payload = dict(data or {})
        payload["csrf_token"] = "test-token"
        return client.post(url, data=payload, follow_redirects=follow_redirects)

    return _post


@pytest.fixture
def make_room(conn):
    def _make(number="101", room_type="Double", capacity="2", rate="100.00", description=""):
        return services.create_room(
            conn,
            {"room_number": number, "room_type": room_type, "capacity": capacity, "rate": rate, "description": description},
        )

    return _make


@pytest.fixture
def make_guest(conn):
    def _make(name="Test Guest", phone="+1 555-0100", email="test@example.com"):
        return services.create_guest(conn, {"full_name": name, "phone": phone, "email": email})

    return _make


@pytest.fixture
def book(conn):
    """Create a reservation with day offsets from today."""

    def _book(guest_id, room_id, start, end, num_guests="1", notes=""):
        return services.create_reservation(
            conn,
            {
                "guest_id": str(guest_id),
                "room_id": str(room_id),
                "check_in": day(start),
                "check_out": day(end),
                "num_guests": num_guests,
                "notes": notes,
            },
            TODAY,
        )

    return _book
