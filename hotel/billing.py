"""Bill calculation. Pure functions, no database or Flask code.

All money is handled as integer minor units (cents/paise) and percentages as
Decimal, so results are exact and repeatable.

How a bill is built (a deliberately simple, documented model):

    room charge   = nightly rate x number of nights
    extras        = sum of extra line items (room service, parking, ...)
    subtotal      = room charge + extras
    service       = subtotal x service %           (rounded half-up to a cent)
    tax           = (subtotal + service) x tax %   (rounded half-up to a cent)
    total         = subtotal + service + tax

This is a demo model, not tax advice: real tax rules differ by country.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Bill:
    nights: int
    rate_cents: int
    room_charge_cents: int
    extras: tuple  # tuple of (description, amount_cents)
    extras_total_cents: int
    subtotal_cents: int
    service_percent: Decimal
    service_cents: int
    tax_percent: Decimal
    tax_cents: int
    total_cents: int


def nights_between(check_in: date, check_out: date) -> int:
    """Number of nights stayed: check-out day itself is not charged."""
    return (check_out - check_in).days


def percent_of(amount_cents: int, percent) -> int:
    """`percent`% of an amount, rounded half-up to a whole cent."""
    value = Decimal(amount_cents) * Decimal(str(percent)) / Decimal(100)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def compute_bill(rate_cents: int, nights: int, tax_percent, service_percent, extras=()) -> Bill:
    if nights < 1:
        raise ValueError("A stay must be at least one night.")
    if rate_cents < 0:
        raise ValueError("The nightly rate cannot be negative.")
    tax_percent = Decimal(str(tax_percent))
    service_percent = Decimal(str(service_percent))
    if not (Decimal(0) <= tax_percent <= Decimal(100)) or not (
        Decimal(0) <= service_percent <= Decimal(100)
    ):
        raise ValueError("Percentages must be between 0 and 100.")

    extras = tuple((str(desc), int(cents)) for desc, cents in extras)
    if any(cents < 0 for _, cents in extras):
        raise ValueError("Extra charges cannot be negative.")

    room_charge = rate_cents * nights
    extras_total = sum(cents for _, cents in extras)
    subtotal = room_charge + extras_total
    service = percent_of(subtotal, service_percent)
    tax = percent_of(subtotal + service, tax_percent)
    return Bill(
        nights=nights,
        rate_cents=rate_cents,
        room_charge_cents=room_charge,
        extras=extras,
        extras_total_cents=extras_total,
        subtotal_cents=subtotal,
        service_percent=service_percent,
        service_cents=service,
        tax_percent=tax_percent,
        tax_cents=tax,
        total_cents=subtotal + service + tax,
    )


# ---------------------------------------------------------------- formatting
def format_cents(cents: int) -> str:
    """12345 -> '123.45' (no currency symbol, no thousands separator)."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def format_money(cents: int, symbol: str = "") -> str:
    """123456 -> '1,234.56' with an optional currency symbol in front."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{symbol}{cents // 100:,}.{cents % 100:02d}"


def format_percent(value) -> str:
    """Decimal('12.50') -> '12.5', Decimal('10') -> '10'."""
    return format(Decimal(str(value)).normalize(), "f")
