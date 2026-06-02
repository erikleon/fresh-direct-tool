"""Order-history retrieval via GraphQL interception, plus an offline fallback.

FreshDirect's account pages are a React SPA backed by a GraphQL API
(``POST /graphql``). Rather than scrape hashed-classname DOM or replay query
strings (which can change between builds / be persisted queries), we drive the
real app and read its GraphQL **responses** off the wire by operation name:

- ``ordersHistory`` (on ``/account/history``) → the list of past orders.
- ``order`` (on ``/account/order_details/{id}``) → that order's line items.

The paste fallback keeps the product usable if automation is ever blocked, and —
unlike the live path — it is fully unit-tested because it touches no network.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser

from app.config import Settings
from app.freshdirect.base import Address, Order, OrderItem, SessionExpired
from app.freshdirect.session import browser_context, ensure_logged_in

# --------------------------------------------------------------------------- #
# Live retrieval (GraphQL interception)
# --------------------------------------------------------------------------- #


class _GraphQLSink:
    """Collects the latest payload for each GraphQL operation seen on the page.

    A single ``/graphql`` response may be one object or a batched array of them;
    both are flattened here, keyed by the operation name under ``data``.
    """

    def __init__(self) -> None:
        self.ops: dict[str, object] = {}

    def handle(self, response) -> None:
        if "/graphql" not in response.url:
            return
        try:
            body = response.json()
        except Exception:
            return
        for obj in body if isinstance(body, list) else [body]:
            data = obj.get("data") if isinstance(obj, dict) else None
            if isinstance(data, dict):
                for key, value in data.items():
                    if value is not None:
                        self.ops[key] = value


def fetch_order_history(
    settings: Settings,
    limit: int = 10,
    with_details: bool = False,
    headed: bool = False,
) -> list[Order]:
    """Return up to ``limit`` recent orders, newest first.

    Boots the SPA at the homepage (direct deep-links to the React routes render
    an error page), then navigates to the order-history view and reads the
    ``ordersHistory`` GraphQL response. With ``with_details`` each order is
    enriched from its detail page's ``order`` response. ``headed=True`` runs
    visibly, which can clear a bot challenge a headless reuse occasionally trips.
    """
    with browser_context(settings, headless=not headed) as (_context, page):
        sink = _GraphQLSink()
        page.on("response", sink.handle)

        # Boot the SPA, then client-route to order history.
        page.goto(settings.fd_base_url, wait_until="domcontentloaded",
                  timeout=settings.nav_timeout_ms)
        page.wait_for_timeout(2000)
        page.goto(settings.fd_account_url, wait_until="domcontentloaded",
                  timeout=settings.nav_timeout_ms)
        history = _wait_for_op(page, sink, "ordersHistory", settings)

        if history is None:
            ensure_logged_in(page, settings)  # raises if the session expired
            raise RuntimeError(
                "Reached the order-history page but never saw the ordersHistory "
                "GraphQL response — the API may have changed."
            )

        infos = (history or {}).get("ordersInfo") or []
        orders = [_parse_order_summary(info) for info in infos[:limit]]

        if with_details:
            for order in orders:
                order.items = _fetch_order_lines(page, sink, settings, order.order_id)

    return orders


def _fetch_order_lines(page, sink: _GraphQLSink, settings: Settings, order_id: str) -> list[OrderItem]:
    sink.ops.pop("order", None)
    page.goto(
        f"{settings.fd_base_url}/account/order_details/{order_id}",
        wait_until="domcontentloaded",
        timeout=settings.nav_timeout_ms,
    )
    detail = _wait_for_op(page, sink, "order", settings)
    lines = (detail or {}).get("cartLines") or []
    return [_parse_cart_line(line) for line in lines if line]


def _wait_for_op(page, sink: _GraphQLSink, op: str, settings: Settings):
    """Poll until the named GraphQL operation has been captured, or time out."""
    deadline_ticks = max(1, settings.nav_timeout_ms // 500)
    for _ in range(deadline_ticks):
        if op in sink.ops:
            return sink.ops[op]
        page.wait_for_timeout(500)
    return sink.ops.get(op)


def _parse_order_summary(info: dict) -> Order:
    return Order(
        order_id=str(info.get("orderId")),
        ordered_on=_parse_date(info.get("requestedDate")) or date.today(),
        delivery_start=_parse_dt(info.get("deliveryStart")),
        delivery_end=_parse_dt(info.get("deliveryEnd")),
        status=info.get("orderStatus"),
        address=_parse_address(info.get("address")),
        total=_to_decimal(info.get("orderTotal")),
    )


def _parse_address(addr: dict | None) -> Address | None:
    if not addr:
        return None
    return Address(
        address1=_clean(addr.get("address1")),
        apartment=_clean(addr.get("apartment")),
        city=_clean(addr.get("city")),
        state=_clean(addr.get("state")),
        zip_code=_clean(addr.get("zipCode")),
    )


def _parse_cart_line(line: dict) -> OrderItem:
    product = line.get("product") or {}
    return OrderItem(
        name=product.get("productName") or "(unknown)",
        product_id=product.get("productId"),
        brand=_clean(product.get("brandName")),
        quantity=_to_float(line.get("quantity"), default=1.0),
        total_price=_price(line.get("price")),
        category=_clean(line.get("departmentLabel")),
        substituted=str(line.get("substituted")).lower() == "true",
    )


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


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None if text.lower() != "none" else None


def _parse_date(text) -> date | None:
    dt = _parse_dt(text)
    return dt.date() if dt else None


def _parse_dt(text):
    if not text or str(text).lower() == "none":
        return None
    try:
        # FreshDirect dates look like "Thu May 28 19:00:00 EDT 2026"; ignoretz
        # avoids ambiguity around named zones like EDT.
        return dateparser.parse(str(text), fuzzy=True, ignoretz=True)
    except (ValueError, OverflowError):
        return None


def _price(price: dict | None) -> Decimal | None:
    if isinstance(price, dict):
        return _to_decimal(price.get("value"))
    return None


def _to_decimal(token) -> Decimal | None:
    if token is None:
        return None
    try:
        return Decimal(str(token).replace(",", "").replace("$", ""))
    except (InvalidOperation, AttributeError):
        return None


def _to_float(token, default: float = 0.0) -> float:
    try:
        return float(str(token))
    except (ValueError, TypeError):
        return default
