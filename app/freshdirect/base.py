"""Adapter contract + domain models shared across the FreshDirect package.

These Pydantic models are the *stable* boundary the rest of the app codes
against. Scraping selectors may churn; these shapes should not.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SessionExpired(RuntimeError):
    """Raised when a saved session is missing/expired and re-login is required."""


class Address(BaseModel):
    """A delivery address, as carried on each order."""

    address1: str | None = None
    apartment: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None

    def one_line(self) -> str:
        parts = [self.address1, self.apartment, self.city, self.state, self.zip_code]
        return ", ".join(p for p in parts if p)


class OrderItem(BaseModel):
    """A single line on a past order (from ``order.cartLines``)."""

    name: str
    product_id: str | None = None
    brand: str | None = None
    quantity: float = 1
    unit: str | None = None  # e.g. "ea", "lb"
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    category: str | None = None  # FreshDirect departmentLabel
    substituted: bool = False


class Order(BaseModel):
    """A historical FreshDirect order (from ``ordersHistory.ordersInfo``)."""

    order_id: str
    ordered_on: date
    delivered_on: date | None = None
    delivery_start: datetime | None = None
    delivery_end: datetime | None = None
    status: str | None = None  # e.g. DELIVERED, PENDING
    address: Address | None = None
    subtotal: Decimal | None = None
    total: Decimal | None = None
    items: list[OrderItem] = Field(default_factory=list)


@runtime_checkable
class FreshDirectAdapter(Protocol):
    """What the planner needs from FreshDirect. Implemented by the Playwright
    adapter in production and by fixtures/fakes in tests."""

    def fetch_order_history(
        self, limit: int = 10, with_details: bool = False
    ) -> list[Order]:
        """Return up to ``limit`` most-recent orders, newest first.

        With ``with_details`` each order is enriched with its line items (one
        extra page load per order). Raises :class:`SessionExpired` if not
        logged in.
        """
        ...
