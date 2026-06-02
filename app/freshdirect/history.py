"""Offline fallback: parse a copy-pasted order export into structured orders.

The live path lives in :mod:`app.freshdirect.client` (GraphQL interception). This
fallback keeps the product usable if automation is ever blocked, and — touching
no network — it is fully unit-tested.
"""

from __future__ import annotations

import re
from datetime import date

from app.freshdirect.base import Order, OrderItem
from app.freshdirect.parse import parse_date, to_decimal

_ORDER_HEADER = re.compile(r"^\s*order\b", re.IGNORECASE)
_ID = re.compile(r"#\s*([A-Za-z0-9-]+)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})")
_MONEY = re.compile(r"\$\s*([\d,]+\.\d{2})")
_LEADING_QTY = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:x|×)?\s+")


def parse_pasted_history(text: str) -> list[Order]:
    """Parse a copy-pasted order export into structured orders.

    Expected shape (flexible — see ``tests/test_paste_parser.py``)::

        Order #12345  2026-05-20  $142.50
          2  Organic 2% Milk, half gallon   $4.99  $9.98
          1  Chicken Thighs, boneless              $8.49

    Header lines start with "Order" and carry an ``#id``, a date, and a trailing
    ``$total``. Item lines carry an optional leading quantity, a name, and one or
    two trailing money amounts (one → total; two → unit then total).
    """
    orders: list[Order] = []
    current: Order | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        if _ORDER_HEADER.match(line):
            if current is not None:
                orders.append(current)
            current = _parse_header(line)
            continue

        if current is not None:
            item = _parse_item_line(line)
            if item is not None:
                current.items.append(item)

    if current is not None:
        orders.append(current)
    return orders


def _parse_header(line: str) -> Order:
    id_match = _ID.search(line)
    date_match = _DATE.search(line)
    money = _MONEY.findall(line)
    return Order(
        order_id=id_match.group(1) if id_match else "unknown",
        ordered_on=parse_date(date_match.group(1)) if date_match else date.today(),
        total=to_decimal(money[-1]) if money else None,
    )


def _parse_item_line(line: str) -> OrderItem | None:
    name_part = line.strip()

    qty = 1.0
    qty_match = _LEADING_QTY.match(line)
    if qty_match:
        qty = float(qty_match.group(1))
        name_part = line[qty_match.end():].strip()

    prices = _MONEY.findall(name_part)
    name = _MONEY.sub("", name_part).strip().rstrip("·-—").strip()
    if not name:
        return None

    unit_price = total_price = None
    if len(prices) == 1:
        total_price = to_decimal(prices[0])
    elif len(prices) >= 2:
        unit_price = to_decimal(prices[0])
        total_price = to_decimal(prices[-1])

    return OrderItem(
        name=name, quantity=qty, unit_price=unit_price, total_price=total_price
    )
