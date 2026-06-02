"""Tests for the offline paste-fallback parser (no network)."""

from datetime import date
from decimal import Decimal

from app.freshdirect.history import parse_pasted_history

SAMPLE = """
Order #12345  2026-05-20  $142.50
  2  Organic 2% Milk, half gallon   $4.99  $9.98
  1  Chicken Thighs, boneless              $8.49
  3 x Bananas                              $1.74

Order #12346  2026-05-13  $98.20
  1  Sourdough Loaf  $5.49
"""


def test_splits_into_orders():
    orders = parse_pasted_history(SAMPLE)
    assert len(orders) == 2
    assert [o.order_id for o in orders] == ["12345", "12346"]


def test_header_fields():
    first = parse_pasted_history(SAMPLE)[0]
    assert first.ordered_on == date(2026, 5, 20)
    assert first.total == Decimal("142.50")
    assert len(first.items) == 3


def test_quantity_and_dual_price():
    milk = parse_pasted_history(SAMPLE)[0].items[0]
    assert milk.name == "Organic 2% Milk, half gallon"
    assert milk.quantity == 2
    assert milk.unit_price == Decimal("4.99")
    assert milk.total_price == Decimal("9.98")


def test_single_price_is_total():
    thighs = parse_pasted_history(SAMPLE)[0].items[1]
    assert thighs.name == "Chicken Thighs, boneless"
    assert thighs.quantity == 1
    assert thighs.unit_price is None
    assert thighs.total_price == Decimal("8.49")


def test_times_x_quantity_notation():
    bananas = parse_pasted_history(SAMPLE)[0].items[2]
    assert bananas.name == "Bananas"
    assert bananas.quantity == 3
    assert bananas.total_price == Decimal("1.74")


def test_second_order_items():
    bread = parse_pasted_history(SAMPLE)[1].items[0]
    assert bread.name == "Sourdough Loaf"
    assert bread.total_price == Decimal("5.49")


def test_empty_input():
    assert parse_pasted_history("") == []


def test_us_date_format():
    orders = parse_pasted_history("Order #9  5/3/2026  $10.00\n  1 Eggs $3.00")
    assert orders[0].ordered_on == date(2026, 5, 3)


def test_thousands_separator_in_total():
    orders = parse_pasted_history("Order #9  2026-01-01  $1,234.56\n  1 Caviar $1,234.56")
    assert orders[0].total == Decimal("1234.56")
    assert orders[0].items[0].total_price == Decimal("1234.56")
