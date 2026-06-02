"""Pure parsing helpers for FreshDirect GraphQL payloads and pasted text.

No browser or I/O here — just JSON/text in, domain models out — so it is cheap to
unit-test and is shared by the live client and the paste fallback alike.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser

from app.freshdirect.base import Address, Order, OrderItem


class GraphQLSink:
    """Collects the latest payload for each GraphQL operation seen on a page.

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

    def pop(self, op: str) -> None:
        self.ops.pop(op, None)


def parse_order_summary(info: dict) -> Order:
    """One entry of ``ordersHistory.ordersInfo`` → an :class:`Order` (no items)."""
    return Order(
        order_id=str(info.get("orderId")),
        ordered_on=parse_date(info.get("requestedDate")) or date.today(),
        delivery_start=parse_dt(info.get("deliveryStart")),
        delivery_end=parse_dt(info.get("deliveryEnd")),
        status=info.get("orderStatus"),
        address=parse_address(info.get("address")),
        total=to_decimal(info.get("orderTotal")),
    )


def parse_address(addr: dict | None) -> Address | None:
    if not addr:
        return None
    return Address(
        address1=clean(addr.get("address1")),
        apartment=clean(addr.get("apartment")),
        city=clean(addr.get("city")),
        state=clean(addr.get("state")),
        zip_code=clean(addr.get("zipCode")),
    )


def parse_cart_line(line: dict) -> OrderItem:
    """One entry of ``order.cartLines`` → an :class:`OrderItem`."""
    product = line.get("product") or {}
    return OrderItem(
        name=product.get("productName") or "(unknown)",
        product_id=product.get("productId"),
        brand=clean(product.get("brandName")),
        quantity=to_float(line.get("quantity"), default=1.0),
        total_price=price_value(line.get("price")),
        category=clean(line.get("departmentLabel")),
        substituted=str(line.get("substituted")).lower() == "true",
    )


# --------------------------------------------------------------------------- #
# Scalar helpers
# --------------------------------------------------------------------------- #


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return text


def parse_date(text) -> date | None:
    dt = parse_dt(text)
    return dt.date() if dt else None


def parse_dt(text) -> datetime | None:
    if not text or str(text).lower() == "none":
        return None
    try:
        # FreshDirect dates look like "Thu May 28 19:00:00 EDT 2026"; ignoretz
        # avoids ambiguity around named zones like EDT.
        return dateparser.parse(str(text), fuzzy=True, ignoretz=True)
    except (ValueError, OverflowError):
        return None


def price_value(price: dict | None) -> Decimal | None:
    if isinstance(price, dict):
        return to_decimal(price.get("value"))
    return None


def to_decimal(token) -> Decimal | None:
    if token is None:
        return None
    try:
        return Decimal(str(token).replace(",", "").replace("$", ""))
    except (InvalidOperation, AttributeError):
        return None


def to_float(token, default: float = 0.0) -> float:
    try:
        return float(str(token))
    except (ValueError, TypeError):
        return default
