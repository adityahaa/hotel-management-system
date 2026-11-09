"""SQLite connection handling for the hotel application."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Example defaults. Everything here can be changed on the Settings page.
DEFAULT_SETTINGS = {
    "hotel_name": "Sample Hotel",
    "hotel_address": "123 Example Street, Sample City",
    "currency_symbol": "₹",  # the rupee sign; change it on the Settings page
    "tax_name": "Tax",
    "tax_percent": "10",
    "service_percent": "5",
}


def connect(path) -> sqlite3.Connection:
    """Open a connection in autocommit mode.

    Writes are wrapped in `transaction()` below so that each change is atomic.
    """
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK if anything raises.

    BEGIN IMMEDIATE takes the write lock up front, so "check availability, then
    insert" cannot be interleaved with another request doing the same thing.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables (if missing) and insert default settings (if missing)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    with transaction(conn):
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    with app.app_context():
        conn = connect(app.config["DATABASE"])
        try:
            init_schema(conn)
        finally:
            conn.close()
