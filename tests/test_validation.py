"""Input validation: required fields, dates, amounts."""
from datetime import date
from decimal import Decimal

import pytest

from hotel.validation import FieldReader, ValidationError, parse_iso_date


@pytest.mark.parametrize("raw", ["120", "120.5", "120.50", "0.01", "999999999.99"])
def test_money_accepts_plain_amounts(raw):
    r = FieldReader({"a": raw})
    assert r.money("a", "Amount", max_cents=10**12) == int(Decimal(raw) * 100)
    assert not r.errors


@pytest.mark.parametrize(
    "raw", ["", "abc", "NaN", "Infinity", "1e3", "1_000", "-5", "1,000", "1.234", "12.", ".5", "0x10", "1000000000"]
)
def test_money_rejects_bad_input(raw):
    r = FieldReader({"a": raw})
    assert r.money("a", "Amount", min_cents=1) is None
    assert "a" in r.errors


def test_money_zero_and_limits():
    r = FieldReader({"a": "0"})
    assert r.money("a", "Amount", min_cents=1) is None
    assert "greater than zero" in r.errors["a"]
    r = FieldReader({"a": "1000000.01"})
    assert r.money("a", "Amount") is None
    assert "too large" in r.errors["a"]


def test_percent_validation():
    ok = FieldReader({"p": "12.5"})
    assert ok.percent("p", "Tax") == Decimal("12.5")
    for bad in ["", "101", "-1", "abc", "10.555", "NaN"]:
        r = FieldReader({"p": bad})
        assert r.percent("p", "Tax") is None
        assert "p" in r.errors


def test_parse_iso_date_is_strict():
    assert parse_iso_date("2026-03-10") == date(2026, 3, 10)
    for bad in ["", "10/03/2026", "2026-3-10", "2026-02-30", "2026-13-01", "tomorrow", None, "2026-03-10 "[:-1] + "x"]:
        assert parse_iso_date(bad) is None


def test_integer_bounds():
    r = FieldReader({"n": "3"})
    assert r.integer("n", "Guests", min_value=1, max_value=10) == 3
    for bad in ["0", "11", "-1", "2.5", "abc", ""]:
        r = FieldReader({"n": bad})
        assert r.integer("n", "Guests", min_value=1, max_value=10) is None
        assert "n" in r.errors


def test_text_required_trimmed_and_length_limited():
    r = FieldReader({"t": "   Ada   Lovelace  "})
    assert r.text("t", "Name", max_len=50, required=True) == "Ada Lovelace"
    r = FieldReader({"t": "   "})
    r.text("t", "Name", required=True)
    assert r.errors["t"] == "Name is required."
    r = FieldReader({"t": "x" * 11})
    r.text("t", "Name", max_len=10)
    assert "10 characters" in r.errors["t"]


def test_email_and_phone():
    r = FieldReader({"e": "a@example.com", "p": "+1 (555) 010-0199"})
    r.email("e")
    r.phone("p", required=True)
    assert not r.errors
    r = FieldReader({"e": "not-an-email", "p": "12"})
    r.email("e")
    r.phone("p", required=True)
    assert set(r.errors) == {"e", "p"}
    r = FieldReader({"p": "555-abcd-0100"})
    r.phone("p")
    assert "p" in r.errors


def test_check_raises_with_all_errors():
    r = FieldReader({})
    r.text("a", "A", required=True)
    r.text("b", "B", required=True)
    with pytest.raises(ValidationError) as info:
        r.check()
    assert set(info.value.errors) == {"a", "b"}
