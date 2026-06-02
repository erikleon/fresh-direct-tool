"""Order-history retrieval: live Playwright scrape + an offline paste fallback.

The live scraper's CSS selectors are a *best-effort starting point*; FreshDirect's
markup is confirmed and tuned during the Phase 0 spike (the selectors are grouped
at the top of :func:`scrape_order_history` for exactly that reason). The paste
fallback keeps the product usable if automation is ever blocked, and — unlike the
scraper — it is fully unit-tested because it touches no network.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser

from app.config import Settings
from app.freshdirect.base import Order, OrderItem
from app.freshdirect.session import browser_context, ensure_logged_in

# --------------------------------------------------------------------------- #
# Live scrape (Playwright)
# --------------------------------------------------------------------------- #

# Selectors are intentionally centralized; confirm against the live DOM in Phase 0.
_SEL = {
    "order_card": "[data-testid='order-card'], .order-history-item, .past-order",
    "order_id": "[data-testid='order-number'], .order-number",
    "order_date": "[data-testid='order-date'], .order-date, time",
    "order_total": "[data-testid='order-total'], .order-total",
    "item_row": "[data-testid='order-line'], .order-line, .line-item",
    "item_name": "[data-testid='product-name'], .product-name, .item-name",
    "item_qty": "[data-testid='quantity'], .quantity, .qty",
    "item_price": "[data-testid='line-price'], .line-price, .item-price",
}


def scrape_order_history(settings: Settings, limit: int = 10) -> list[Order]:
    """Scrape up to ``limit`` recent orders from the authenticated account.

    Raises :class:`~app.freshdirect.base.SessionExpired` via ``ensure_logged_in``
    when the saved session is no longer valid.
    """
    orders: list[Order] = []
    with browser_context(settings) as (_context, page):
        page.goto(settings.fd_account_url, timeout=settings.nav_timeout_ms)
        ensure_logged_in(page, settings)
        page.wait_for_load_state("networkidle")

        cards = page.locator(_SEL["order_card"])
        count = min(cards.count(), limit)
        for i in range(count):
            card = cards.nth(i)
            orders.append(_parse_order_card(card))
    return orders


def _parse_order_card(card) -> Order:
    """Pull one order out of a card locator, tolerating missing fields."""

    def text(sel: str) -> str | None:
        loc = card.locator(sel).first
        return loc.inner_text().strip() if loc.count() else None

    order_id = _clean_id(text(_SEL["order_id"])) or "unknown"
    ordered_on = _parse_date(text(_SEL["order_date"])) or date.today()
    total = _money(text(_SEL["order_total"]))

    items: list[OrderItem] = []
    rows = card.locator(_SEL["item_row"])
    for j in range(rows.count()):
        row = rows.nth(j)
        name = row.locator(_SEL["item_name"]).first
        if not name.count():
            continue
        qty_loc = row.locator(_SEL["item_qty"]).first
        price_loc = row.locator(_SEL["item_price"]).first
        items.append(
            OrderItem(
                name=name.inner_text().strip(),
                quantity=_qty(qty_loc.inner_text()) if qty_loc.count() else 1,
                total_price=_money(price_loc.inner_text()) if price_loc.count() else None,
            )
        )

    return Order(order_id=order_id, ordered_on=ordered_on, total=total, items=items)


# --------------------------------------------------------------------------- #
# Paste fallback (offline, fully tested)
# --------------------------------------------------------------------------- #

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
        ordered_on=_parse_date(date_match.group(1)) if date_match else date.today(),
        total=_to_decimal(money[-1]) if money else None,
    )


def _parse_item_line(line: str) -> OrderItem | None:
    name_part = line.strip()

    qty = 1.0
    qty_match = _LEADING_QTY.match(line)
    if qty_match:
        qty = float(qty_match.group(1))
        name_part = line[qty_match.end():].strip()

    prices = _MONEY.findall(name_part)
    # Strip trailing money tokens from the name.
    name = _MONEY.sub("", name_part).strip().rstrip("·-—").strip()
    if not name:
        return None

    unit_price = total_price = None
    if len(prices) == 1:
        total_price = _to_decimal(prices[0])
    elif len(prices) >= 2:
        unit_price = _to_decimal(prices[0])
        total_price = _to_decimal(prices[-1])

    return OrderItem(
        name=name, quantity=qty, unit_price=unit_price, total_price=total_price
    )


# --------------------------------------------------------------------------- #
# Shared parsing helpers
# --------------------------------------------------------------------------- #


def _clean_id(text: str | None) -> str | None:
    if not text:
        return None
    m = _ID.search(text)
    return m.group(1) if m else text.strip()


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return dateparser.parse(text, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None


def _money(text: str | None) -> Decimal | None:
    if not text:
        return None
    m = _MONEY.search(text)
    return _to_decimal(m.group(1)) if m else None


def _qty(text: str | None) -> float:
    if not text:
        return 1.0
    m = re.search(r"\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else 1.0


def _to_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
