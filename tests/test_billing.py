"""Billing calculations: exact money maths."""
from datetime import date
from decimal import Decimal

import pytest

from hotel.billing import (
    compute_bill,
    format_cents,
    format_money,
    format_percent,
    nights_between,
    percent_of,
)


def test_nights_exclude_the_checkout_day():
    assert nights_between(date(2026, 3, 10), date(2026, 3, 13)) == 3
    assert nights_between(date(2026, 3, 10), date(2026, 3, 11)) == 1


def test_nights_across_month_and_leap_day():
    assert nights_between(date(2028, 2, 28), date(2028, 3, 1)) == 2  # 2028 is a leap year
    assert nights_between(date(2026, 12, 30), date(2027, 1, 2)) == 3


def test_basic_bill_with_service_and_tax():
    # 3 nights x 100.00 = 300.00; service 5% = 15.00; tax 10% of 315.00 = 31.50
    bill = compute_bill(10000, 3, Decimal("10"), Decimal("5"))
    assert bill.room_charge_cents == 30000
    assert bill.subtotal_cents == 30000
    assert bill.service_cents == 1500
    assert bill.tax_cents == 3150
    assert bill.total_cents == 34650


def test_extras_are_part_of_the_taxable_subtotal():
    # subtotal 300.00 + 20.00 = 320.00; service 16.00; tax 10% of 336.00 = 33.60
    bill = compute_bill(10000, 3, 10, 5, extras=[("Room service", 2000)])
    assert bill.extras_total_cents == 2000
    assert bill.subtotal_cents == 32000
    assert bill.service_cents == 1600
    assert bill.tax_cents == 3360
    assert bill.total_cents == 36960
    assert bill.extras == (("Room service", 2000),)


def test_zero_tax_and_zero_service():
    bill = compute_bill(8950, 2, 0, 0)
    assert bill.service_cents == 0
    assert bill.tax_cents == 0
    assert bill.total_cents == 17900


def test_rounding_is_half_up_not_bankers():
    # 10.05 x 10% = 1.005 -> 1.01 (half-up). Banker's rounding would give 1.00.
    assert percent_of(1005, 10) == 101
    # 10.04 x 10% = 1.004 -> 1.00
    assert percent_of(1004, 10) == 100


def test_fractional_percentages():
    # 12.5% of 200.00 = 25.00; 7.25% of 100.00 = 7.25
    assert percent_of(20000, Decimal("12.5")) == 2500
    assert percent_of(10000, Decimal("7.25")) == 725


def test_total_is_always_the_sum_of_its_parts():
    for rate in (1, 99, 3333, 12345, 99999):
        for nights in (1, 2, 7, 30):
            for tax, service in ((0, 0), (5, 0), (18, 5), ("12.5", "10"), (100, 100)):
                bill = compute_bill(rate, nights, tax, service, extras=[("x", 101)])
                assert bill.total_cents == bill.subtotal_cents + bill.service_cents + bill.tax_cents
                assert bill.subtotal_cents == bill.room_charge_cents + bill.extras_total_cents


@pytest.mark.parametrize("nights", [0, -1])
def test_stay_must_be_at_least_one_night(nights):
    with pytest.raises(ValueError):
        compute_bill(10000, nights, 10, 5)


def test_rejects_bad_percentages_and_negative_amounts():
    with pytest.raises(ValueError):
        compute_bill(10000, 1, 101, 0)
    with pytest.raises(ValueError):
        compute_bill(10000, 1, 0, -1)
    with pytest.raises(ValueError):
        compute_bill(-5, 1, 0, 0)
    with pytest.raises(ValueError):
        compute_bill(10000, 1, 0, 0, extras=[("refund", -100)])


def test_formatting():
    assert format_cents(12345) == "123.45"
    assert format_cents(5) == "0.05"
    assert format_money(123456789, "$") == "$1,234,567.89"
    assert format_money(0) == "0.00"
    assert format_money(-250, "$") == "-$2.50"
    assert format_percent(Decimal("12.50")) == "12.5"
    assert format_percent("10") == "10"
    assert format_percent(Decimal("0")) == "0"
