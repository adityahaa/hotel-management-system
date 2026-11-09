-- Hotel Management System: SQLite schema.
-- Money is stored as whole minor units (e.g. cents/paise) in *_cents columns,
-- so there are no floating-point rounding surprises.
-- Dates are stored as ISO text (YYYY-MM-DD), which sorts and compares correctly.

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    room_type   TEXT    NOT NULL,
    capacity    INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 10),
    rate_cents  INTEGER NOT NULL CHECK (rate_cents > 0),
    description TEXT    NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS guests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name  TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL,
    address    TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reservations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guest_id       INTEGER NOT NULL REFERENCES guests(id),
    room_id        INTEGER NOT NULL REFERENCES rooms(id),
    check_in       TEXT    NOT NULL,
    check_out      TEXT    NOT NULL,
    num_guests     INTEGER NOT NULL DEFAULT 1 CHECK (num_guests >= 1),
    status         TEXT    NOT NULL DEFAULT 'confirmed'
                   CHECK (status IN ('confirmed', 'checked_in', 'checked_out', 'cancelled')),
    -- Snapshots taken when the booking is made, so later changes to the room
    -- rate or to the tax settings do not silently change an existing bill.
    rate_cents      INTEGER NOT NULL CHECK (rate_cents > 0),
    tax_percent     TEXT    NOT NULL,
    service_percent TEXT    NOT NULL,
    notes          TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    checked_in_at  TEXT,
    checked_out_at TEXT,
    cancelled_at   TEXT,
    CHECK (check_out > check_in)
);

CREATE INDEX IF NOT EXISTS idx_reservations_room_dates
    ON reservations (room_id, check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_reservations_status
    ON reservations (status);
CREATE INDEX IF NOT EXISTS idx_reservations_guest
    ON reservations (guest_id);

-- Extra line items on a bill (room service, parking, ...).
CREATE TABLE IF NOT EXISTS charges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    description    TEXT    NOT NULL,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_charges_reservation ON charges (reservation_id);

-- Safety net: even if application code had a bug, the database itself refuses
-- a booking that overlaps another active booking for the same room.
-- Stays are half-open ranges [check_in, check_out): a guest leaving on the 5th
-- does not clash with another guest arriving on the 5th.
CREATE TRIGGER IF NOT EXISTS trg_reservations_no_overlap
BEFORE INSERT ON reservations
WHEN NEW.status IN ('confirmed', 'checked_in')
BEGIN
    SELECT RAISE(ABORT, 'room already booked for these dates')
    WHERE EXISTS (
        SELECT 1 FROM reservations r
        WHERE r.room_id = NEW.room_id
          AND r.status IN ('confirmed', 'checked_in')
          AND r.check_in  < NEW.check_out
          AND r.check_out > NEW.check_in
    );
END;
