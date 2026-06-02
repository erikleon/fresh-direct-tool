"""Assemble a draft weekly plan from the replenishment forecast.

Restock-only for now (no meal planning yet): take the items predicted due, price
each against the live catalog — preferring the *exact* product the household has
bought before (its historical ``product_id``) — and reconcile against the weekly
budget with cheaper-swap suggestions. The result is a reviewable draft cart.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Callable

from app.analytics import Prediction, replenishment
from app.budget import DraftPlan, PlanLine, reconcile
from app.config import Settings, get_settings
from app.freshdirect.base import Product
from app.freshdirect.client import FreshDirectClient
from app.match import score_candidates

ProgressCb = Callable[[str], None]


def build_plan(
    settings: Settings | None = None,
    horizon_days: int = 7,
    max_items: int = 25,
    budget: float | None = None,
    headed: bool = False,
    progress: ProgressCb | None = None,
) -> DraftPlan:
    """Build a restock draft plan for items due within ``horizon_days``."""
    settings = settings or get_settings()
    say = progress or (lambda _m: None)

    cap = budget if budget is not None else settings.weekly_budget
    cap_cents = int(round(cap * 100)) if cap else None

    due = [
        p for p in replenishment(settings)
        if p.active and p.days_overdue >= -horizon_days
    ][:max_items]
    say(f"{len(due)} items due within {horizon_days}d. Pricing against the catalog…")

    plan = DraftPlan(week_of=date.today(), budget_cap_cents=cap_cents)
    client = FreshDirectClient(settings, headed=headed)

    with client.session() as fd:
        for i, pred in enumerate(due, 1):
            candidates = fd.search_products(pred.name, limit=20)
            line = _line_for(pred, candidates)
            if line is not None:
                plan.lines.append(line)
            say(f"  [{i}/{len(due)}] {pred.name}: "
                f"{'matched' if line and line.product.price else 'no price'}")

    reconcile(plan)
    return plan


def _line_for(pred: Prediction, candidates: list[Product]) -> PlanLine | None:
    """Pick a product for a due item, strongly preferring the exact past SKU."""
    if not candidates:
        return None

    # The replenishment key is the historical product_id when we have one; if it
    # shows up in search, that's exactly what they buy — take it with confidence.
    exact = next((c for c in candidates if c.product_id and c.product_id == pred.key), None)
    if exact is not None and not exact.sold_out:
        pick, confidence = exact, 0.97
    else:
        ranked = score_candidates(pred.name, candidates)
        top = ranked[0]
        pick = top.product
        runner = ranked[1].score if len(ranked) > 1 else 0.0
        sep = min(1.0, max(0.0, top.score - runner) / 0.3)
        confidence = round(max(0.0, min(1.0, top.score)) * (0.6 + 0.4 * sep), 3)

    alternatives = [c for c in candidates if c.sku != pick.sku and c.price is not None]
    status = "due" if pred.days_overdue >= 0 else "soon"
    reason = (
        f"{status} ({'+' if pred.days_overdue >= 0 else ''}{pred.days_overdue}d), "
        f"every {pred.mean_interval_days:g}d, bought ×{pred.times_bought}"
    )
    return PlanLine(
        need=pred.name,
        product=pick,
        quantity=1.0,
        source="replenish",
        confidence=confidence,
        reason=reason,
        alternatives=alternatives,
    )
