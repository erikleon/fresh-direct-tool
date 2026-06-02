"""Backfill FreshDirect history into the local database.

Resumable and idempotent: order summaries are upserted first (one fast page),
then line items are fetched one order at a time and committed immediately, with a
``details_loaded`` flag so a re-run only fetches what's missing. A full backfill
is ~one page load per order, so crashes/timeouts mid-run cost little.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.config import Settings, get_settings
from app.db import init_db, session_scope
from app.freshdirect.base import Order
from app.freshdirect.client import FreshDirectClient
from app.models import OrderItemRow, OrderRow
from app.money import to_cents


@dataclass
class BackfillResult:
    orders_seen: int
    details_fetched: int
    skipped: int


ProgressCb = Callable[[str], None]


def backfill(
    settings: Settings | None = None,
    limit: int = 500,
    headed: bool = False,
    progress: ProgressCb | None = None,
) -> BackfillResult:
    """Sync up to ``limit`` recent orders (with line items) into the DB."""
    settings = settings or get_settings()
    init_db(settings)
    say = progress or (lambda _m: None)

    client = FreshDirectClient(settings, headed=headed)
    details_fetched = 0

    with client.session() as fd, session_scope(settings) as db:
        summaries = fd.order_summaries(limit=limit)
        say(f"Found {len(summaries)} orders. Syncing summaries…")
        for summary in summaries:
            _upsert_summary(db, summary)
        db.commit()

        # Newest-first; skip orders whose line items are already stored.
        pending = [s.order_id for s in summaries if not db.get(OrderRow, s.order_id).details_loaded]
        say(f"{len(summaries) - len(pending)} already detailed; fetching {len(pending)}…")

        for i, order_id in enumerate(pending, 1):
            lines = fd.order_lines(order_id)
            row = db.get(OrderRow, order_id)
            row.items.clear()
            for item in lines:
                row.items.append(_to_item_row(item))
            row.details_loaded = True
            db.add(row)
            db.commit()
            details_fetched += 1
            say(f"  [{i}/{len(pending)}] order {order_id}: {len(lines)} items")

    return BackfillResult(
        orders_seen=len(summaries),
        details_fetched=details_fetched,
        skipped=len(summaries) - details_fetched,
    )


def _upsert_summary(db, order: Order) -> None:
    row = db.get(OrderRow, order.order_id)
    if row is None:
        row = OrderRow(order_id=order.order_id, ordered_on=order.ordered_on)
    row.ordered_on = order.ordered_on
    row.delivery_start = order.delivery_start
    row.delivery_end = order.delivery_end
    row.status = order.status
    if order.address:
        row.address1 = order.address.address1
        row.apartment = order.address.apartment
        row.city = order.address.city
        row.state = order.address.state
        row.zip_code = order.address.zip_code
    row.total_cents = to_cents(order.total)
    db.add(row)


def _to_item_row(item) -> OrderItemRow:
    return OrderItemRow(
        name=item.name,
        product_id=item.product_id,
        brand=item.brand,
        quantity=item.quantity,
        total_cents=to_cents(item.total_price),
        category=item.category,
        substituted=item.substituted,
    )
