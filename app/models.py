"""SQLModel tables for persisted FreshDirect history.

Money is stored as integer cents (see :mod:`app.money`); quantities stay float
because FreshDirect sells by weight (e.g. 0.96 lb). ``details_loaded`` lets the
backfill resume without re-fetching orders whose line items are already stored.
"""

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import JSON, Column
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


class PlanRow(SQLModel, table=True):
    """A persisted weekly plan the household manager reviews and approves."""

    __tablename__ = "plans"

    id: Optional[int] = Field(default=None, primary_key=True)
    week_of: date
    status: str = Field(default="draft", index=True)  # draft | approved | handed_off
    created_at: datetime = Field(default_factory=datetime.utcnow)

    budget_cap_cents: Optional[int] = None

    # Chosen delivery details (set during review).
    address1: Optional[str] = None
    apartment: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    delivery_start: Optional[datetime] = None
    delivery_end: Optional[datetime] = None
    tip_cents: Optional[int] = None

    checkout_url: Optional[str] = None  # set on hand-off

    lines: List["PlanLineRow"] = Relationship(
        back_populates="plan",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class PlanLineRow(SQLModel, table=True):
    """One reviewable line of a plan: the selected product plus alternatives."""

    __tablename__ = "plan_lines"

    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="plans.id", index=True)

    need: str
    source: str = "replenish"  # replenish | meal | manual
    reason: str = ""
    confidence: float = 1.0
    included: bool = True
    quantity: float = 1.0

    original_sku: Optional[str] = None  # the first auto-pick, for "swapped from"
    selected_sku: Optional[str] = None
    selected_name: Optional[str] = None
    selected_brand: Optional[str] = None
    selected_size: Optional[str] = None
    selected_url: Optional[str] = None
    selected_price_cents: Optional[int] = None

    # Candidate products [{sku,name,brand,size,price_cents,url,sold_out}, …].
    alternatives: list = Field(default_factory=list, sa_column=Column(JSON))

    plan: Optional[PlanRow] = Relationship(back_populates="lines")

    @property
    def line_cents(self) -> int:
        cents = self.selected_price_cents or 0
        return int(round(cents * self.quantity))

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.5

    @property
    def swapped(self) -> bool:
        return bool(self.original_sku and self.selected_sku != self.original_sku)


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
