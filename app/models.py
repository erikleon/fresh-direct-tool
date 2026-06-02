"""SQLModel tables for persisted FreshDirect history.

Money is stored as integer cents (see :mod:`app.money`); quantities stay float
because FreshDirect sells by weight (e.g. 0.96 lb). ``details_loaded`` lets the
backfill resume without re-fetching orders whose line items are already stored.
"""

from datetime import date, datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class OrderRow(SQLModel, table=True):
    __tablename__ = "orders"

    order_id: str = Field(primary_key=True)
    ordered_on: date = Field(index=True)
    delivery_start: datetime | None = None
    delivery_end: datetime | None = None
    status: str | None = None

    # Delivery address, flattened.
    address1: str | None = None
    apartment: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None

    total_cents: int | None = None
    details_loaded: bool = Field(default=False, index=True)

    items: List["OrderItemRow"] = Relationship(
        back_populates="order",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    def address_one_line(self) -> str:
        parts = [self.address1, self.apartment, self.city, self.state, self.zip_code]
        return ", ".join(p for p in parts if p)


class OrderItemRow(SQLModel, table=True):
    __tablename__ = "order_items"

    id: int | None = Field(default=None, primary_key=True)
    order_id: str = Field(foreign_key="orders.order_id", index=True)

    name: str
    product_id: str | None = Field(default=None, index=True)
    brand: str | None = None
    quantity: float = 1.0
    total_cents: int | None = None
    category: str | None = Field(default=None, index=True)
    substituted: bool = False

    order: Optional["OrderRow"] = Relationship(back_populates="items")


class ProductAlias(SQLModel, table=True):
    """A learned mapping: a generic need → the household's preferred SKU.

    Written when a human corrects a match (Phase 3) so future matching for the
    same generic item resolves directly instead of guessing.
    """

    __tablename__ = "product_aliases"

    generic_key: str = Field(primary_key=True)  # normalized query text
    sku: str
    product_name: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
