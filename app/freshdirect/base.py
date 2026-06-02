"""Adapter contract + domain models shared across the FreshDirect package.

These Pydantic models are the *stable* boundary the rest of the app codes
against. Scraping selectors may churn; these shapes should not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SessionExpired(RuntimeError):
    """Raised when a saved session is missing/expired and re-login is required."""


class OrderItem(BaseModel):
    """A single line on a past order."""

    name: str
    sku: str | None = None
    quantity: float = 1
    unit: str | None = None  # e.g. "ea", "lb"
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    category: str | None = None


class Order(BaseModel):
    """A historical FreshDirect order."""

    order_id: str
    ordered_on: date
    delivered_on: date | None = None
    address: str | None = None
    subtotal: Decimal | None = None
    total: Decimal | None = None
    items: list[OrderItem] = Field(default_factory=list)


@runtime_checkable
class FreshDirectAdapter(Protocol):
    """What the planner needs from FreshDirect. Implemented by the Playwright
    adapter in production and by fixtures/fakes in tests."""

    def fetch_order_history(self, limit: int = 10) -> list[Order]:
        """Return up to ``limit`` most-recent orders, newest first.

        Raises :class:`SessionExpired` if not logged in.
        """
        ...
