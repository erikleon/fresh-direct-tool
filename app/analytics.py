"""Insights over stored history: spend tracking and replenishment cadence.

The cadence math (:func:`compute_cadence`) is a pure function over purchase dates
so it is unit-tested without a database; the DB-backed wrappers just gather rows
and hand them to it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from statistics import mean

from sqlmodel import select

from app.config import Settings, get_settings
from app.db import session_scope
from app.models import OrderItemRow, OrderRow

# --------------------------------------------------------------------------- #
# Replenishment cadence (pure)
# --------------------------------------------------------------------------- #


@dataclass
class ItemPurchases:
    """How often one item was bought: one date per order it appeared on."""

    key: str
    name: str
    dates: list[date] = field(default_factory=list)


@dataclass
class Prediction:
    key: str
    name: str
    times_bought: int
    mean_interval_days: float
    last_purchased: date
    days_since_last: int
    predicted_next: date
    days_overdue: int  # >0 means due/overdue as of `today`
    active: bool  # still in rotation (bought recently relative to its cadence)

    @property
    def is_due(self) -> bool:
        return self.days_overdue >= 0


def compute_cadence(
    items: list[ItemPurchases],
    today: date,
    min_purchases: int = 3,
    idle_multiple: float = 3.0,
) -> list[Prediction]:
    """Predict each item's next purchase from its historical buying cadence.

    Items bought fewer than ``min_purchases`` distinct times are skipped — too
    little signal to estimate an interval. An item is flagged ``active`` only if
    its last purchase is within ``idle_multiple`` × its mean interval of
    ``today``; anything staler has fallen out of rotation (e.g. a seasonal buy)
    and, while still returned, should not be treated as a live restock signal.
    Results are sorted most-overdue first.
    """
    predictions: list[Prediction] = []
    for item in items:
        dates = sorted(set(item.dates))
        if len(dates) < min_purchases:
            continue
        intervals = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        if not intervals:
            continue
        avg = mean(intervals)
        last = dates[-1]
        days_since_last = (today - last).days
        predicted_next = last.fromordinal(last.toordinal() + round(avg))
        predictions.append(
            Prediction(
                key=item.key,
                name=item.name,
                times_bought=len(dates),
                mean_interval_days=round(avg, 1),
                last_purchased=last,
                days_since_last=days_since_last,
                predicted_next=predicted_next,
                days_overdue=(today - predicted_next).days,
                active=days_since_last <= avg * idle_multiple,
            )
        )
    predictions.sort(key=lambda p: p.days_overdue, reverse=True)
    return predictions


def replenishment(
    settings: Settings | None = None,
    today: date | None = None,
    min_purchases: int = 3,
) -> list[Prediction]:
    """DB-backed replenishment predictions (needs detailed orders)."""
    settings = settings or get_settings()
    today = today or date.today()
    groups: dict[str, ItemPurchases] = {}

    with session_scope(settings) as db:
        rows = db.exec(
            select(OrderItemRow, OrderRow.ordered_on).join(OrderRow)
        ).all()
        for item, ordered_on in rows:
            key = item.product_id or (item.name or "").strip().lower()
            if not key:
                continue
            group = groups.get(key)
            if group is None:
                group = groups[key] = ItemPurchases(key=key, name=item.name)
            group.name = item.name  # keep the most recent display name
            group.dates.append(ordered_on)

    return compute_cadence(list(groups.values()), today=today, min_purchases=min_purchases)


# --------------------------------------------------------------------------- #
# Spend tracking
# --------------------------------------------------------------------------- #


@dataclass
class SpendSummary:
    order_count: int
    total_cents: int
    avg_order_cents: int
    first_date: date | None
    last_date: date | None
    by_month: list[tuple[str, int, int]]  # (YYYY-MM, cents, order_count)
    by_category: list[tuple[str, int]]  # (category, cents), descending


def spend_summary(settings: Settings | None = None) -> SpendSummary:
    settings = settings or get_settings()
    with session_scope(settings) as db:
        orders = db.exec(select(OrderRow)).all()
        items = db.exec(select(OrderItemRow)).all()

    priced = [o for o in orders if o.total_cents is not None]
    total = sum(o.total_cents for o in priced)
    dates = [o.ordered_on for o in orders if o.ordered_on]

    month_cents: dict[str, int] = defaultdict(int)
    month_count: dict[str, int] = defaultdict(int)
    for o in priced:
        ym = o.ordered_on.strftime("%Y-%m")
        month_cents[ym] += o.total_cents
        month_count[ym] += 1

    cat_cents: dict[str, int] = defaultdict(int)
    for it in items:
        if it.total_cents is not None:
            cat_cents[it.category or "Uncategorized"] += it.total_cents

    return SpendSummary(
        order_count=len(orders),
        total_cents=total,
        avg_order_cents=round(total / len(priced)) if priced else 0,
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        by_month=sorted((ym, month_cents[ym], month_count[ym]) for ym in month_cents),
        by_category=sorted(cat_cents.items(), key=lambda kv: kv[1], reverse=True),
    )
