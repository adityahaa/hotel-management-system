"""Input cleaning and validation helpers (no Flask imports, easy to unit test)."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9 ()\-]+$")
ROOM_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,9}$")
# Deliberately strict: plain digits with an optional decimal part. This rejects
# things Python's Decimal would otherwise accept ("NaN", "1e3", "1_000", "-5").
MONEY_RE = re.compile(r"^\d{1,9}(\.\d{1,2})?$")
PERCENT_RE = re.compile(r"^\d{1,3}(\.\d{1,2})?$")
INTEGER_RE = re.compile(r"^\d{1,6}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

MAX_MONEY_CENTS = 100_000_000  # 1,000,000.00


class HotelError(Exception):
    """Base class for expected, user-facing problems."""


class ValidationError(HotelError):
    """One or more form fields are invalid. `errors` maps field name -> message."""

    def __init__(self, errors: dict):
        self.errors = dict(errors)
        super().__init__("; ".join(self.errors.values()))


class ConflictError(HotelError):
    """A booking would overlap an existing one."""

    def __init__(self, message: str, conflicts=()):
        super().__init__(message)
        self.message = message
        self.conflicts = list(conflicts)


class StateError(HotelError):
    """An action is not allowed in the record's current state."""


class NotFoundError(HotelError):
    """The requested record does not exist."""


def parse_iso_date(raw) -> date | None:
    """Parse a strict YYYY-MM-DD string. Returns None if it is not a real date."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed if parsed.isoformat() == raw else None


class FieldReader:
    """Reads and validates form fields, collecting one error per field.

    Usage:
        r = FieldReader(request.form)
        name = r.text("full_name", "Full name", max_len=80, required=True)
        r.check()          # raises ValidationError if anything was wrong
    """

    def __init__(self, data):
        self.data = data
        self.errors: dict = {}
        self.values: dict = {}

    # -- internals
    def _raw(self, name: str) -> str:
        value = self.data.get(name, "")
        if value is None:
            return ""
        return str(value).strip()

    def error(self, name: str, message: str) -> None:
        self.errors.setdefault(name, message)

    def add_errors(self, errors: dict) -> None:
        for name, message in errors.items():
            self.error(name, message)

    def check(self) -> None:
        if self.errors:
            raise ValidationError(self.errors)

    # -- readers
    def text(self, name, label, *, max_len=100, required=False, multiline=False) -> str:
        raw = _CONTROL_CHARS.sub("", self._raw(name))
        if not multiline:
            raw = " ".join(raw.split())
        self.values[name] = raw
        if required and not raw:
            self.error(name, f"{label} is required.")
        elif len(raw) > max_len:
            self.error(name, f"{label} must be {max_len} characters or fewer.")
        return raw

    def email(self, name, label="Email") -> str:
        raw = self.text(name, label, max_len=120)
        if raw and not EMAIL_RE.match(raw):
            self.error(name, f"{label} does not look like a valid address (e.g. name@example.com).")
        return raw

    def phone(self, name, label="Phone", *, required=False) -> str:
        raw = self.text(name, label, max_len=20, required=required)
        if raw:
            digits = sum(ch.isdigit() for ch in raw)
            if not PHONE_RE.match(raw) or digits < 7:
                self.error(name, f"{label} must contain at least 7 digits (spaces, +, - and brackets are allowed).")
        return raw

    def integer(self, name, label, *, min_value=1, max_value=999999, required=True, required_message=None):
        raw = self._raw(name)
        self.values[name] = raw
        if not raw:
            if required:
                self.error(name, required_message or f"{label} is required.")
            return None
        if not INTEGER_RE.match(raw):
            self.error(name, f"{label} must be a whole number.")
            return None
        value = int(raw)
        if value < min_value or value > max_value:
            self.error(name, f"{label} must be between {min_value} and {max_value}.")
            return None
        return value

    def money(self, name, label, *, min_cents=0, max_cents=MAX_MONEY_CENTS, required=True):
        """Returns the amount in cents, or None if missing/invalid."""
        raw = self._raw(name)
        self.values[name] = raw
        if not raw:
            if required:
                self.error(name, f"{label} is required.")
            return None
        if not MONEY_RE.match(raw):
            self.error(name, f"{label} must be a number with at most two decimal places, e.g. 120.50.")
            return None
        cents = int((Decimal(raw) * 100).to_integral_value())
        if cents < min_cents:
            self.error(name, f"{label} must be greater than zero." if min_cents == 1 else f"{label} is too small.")
            return None
        if cents > max_cents:
            self.error(name, f"{label} is too large (maximum {max_cents // 100:,}).")
            return None
        return cents

    def percent(self, name, label) -> Decimal | None:
        raw = self._raw(name)
        self.values[name] = raw
        if not raw:
            self.error(name, f"{label} is required (enter 0 for none).")
            return None
        if not PERCENT_RE.match(raw) or Decimal(raw) > 100:
            self.error(name, f"{label} must be a number between 0 and 100 (at most two decimals).")
            return None
        return Decimal(raw)

    def date(self, name, label) -> date | None:
        raw = self._raw(name)
        self.values[name] = raw
        if not raw:
            self.error(name, f"{label} is required.")
            return None
        parsed = parse_iso_date(raw)
        if parsed is None:
            self.error(name, f"{label} is not a valid date (use YYYY-MM-DD).")
        return parsed

    def choice(self, name, label, choices) -> str | None:
        raw = self._raw(name)
        self.values[name] = raw
        if raw not in choices:
            self.error(name, f"Please choose a valid {label.lower()}.")
            return None
        return raw
