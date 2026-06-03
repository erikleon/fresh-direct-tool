"""Persistence + editing service for review-and-approve plans.

Bridges the in-memory :class:`~app.budget.DraftPlan` (how a plan is built) and the
:class:`~app.models.PlanRow` rows (how it's reviewed, edited, and approved). The
web layer calls these functions; they own all plan mutations.
"""

from __future__ import annotations

from typing import Callable

from app.budget import DraftPlan
from app.config import Settings, get_settings
from app.db import init_db, session_scope
from app.freshdirect.base import Product
from app.models import PlanLineRow, PlanRow
from app.money import to_cents


def _product_dict(p: Product) -> dict:
    return {
        "sku": p.sku,
        "name": p.name,
        "brand": p.brand,
        "size": p.unit_size,
        "price_cents": to_cents(p.price),
        "url": p.url,
        "sold_out": p.sold_out,
    }


def save_draft(draft: DraftPlan, settings: Settings | None = None) -> int:
    """Persist a freshly built DraftPlan as a new 'draft' plan; return its id."""
    settings = settings or get_settings()
    init_db(settings)
    with session_scope(settings) as db:
        plan = PlanRow(week_of=draft.week_of, budget_cap_cents=draft.budget_cap_cents)
        for ln in draft.lines:
            chosen = ln.chosen
            plan.lines.append(
                PlanLineRow(
                    need=ln.need,
                    source=ln.source,
                    reason=ln.reason,
                    confidence=ln.confidence,
                    quantity=ln.quantity,
                    original_sku=ln.product.sku,
                    selected_sku=chosen.sku,
                    selected_name=chosen.name,
                    selected_brand=chosen.brand,
                    selected_size=chosen.unit_size,
                    selected_url=chosen.url,
                    selected_price_cents=to_cents(chosen.price),
                    alternatives=[_product_dict(a) for a in ([ln.product] + ln.alternatives)],
                )
            )
        db.add(plan)
        db.commit()
        return plan.id


def generate_and_save(
    settings: Settings | None = None,
    horizon_days: int = 7,
    max_items: int = 25,
    budget: float | None = None,
    headed: bool = False,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Build a restock draft (live) and persist it; return the new plan id."""
    from app.planner import build_plan

    settings = settings or get_settings()
    draft = build_plan(
        settings, horizon_days=horizon_days, max_items=max_items,
        budget=budget, headed=headed, progress=progress,
    )
    return save_draft(draft, settings)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def get_plan(plan_id: int, settings: Settings | None = None) -> PlanRow | None:
    with session_scope(settings or get_settings()) as db:
        plan = db.get(PlanRow, plan_id)
        if plan:
            plan.lines  # touch relationship before the session closes
            _ = [ln.alternatives for ln in plan.lines]
            db.expunge_all()
        return plan


def latest_plan_id(settings: Settings | None = None) -> int | None:
    from sqlmodel import select

    init_db(settings or get_settings())
    with session_scope(settings or get_settings()) as db:
        row = db.exec(select(PlanRow.id).order_by(PlanRow.created_at.desc())).first()
        return row


# --------------------------------------------------------------------------- #
# Edits (web layer calls these)
# --------------------------------------------------------------------------- #


def set_quantity(plan_id: int, line_id: int, quantity: float, settings: Settings | None = None) -> None:
    _mutate_line(plan_id, line_id, lambda ln: setattr(ln, "quantity", max(0.0, quantity)), settings)


def set_included(plan_id: int, line_id: int, included: bool, settings: Settings | None = None) -> None:
    _mutate_line(plan_id, line_id, lambda ln: setattr(ln, "included", included), settings)


def select_alternative(
    plan_id: int, line_id: int, sku: str, learn: bool = True, settings: Settings | None = None
) -> None:
    """Switch a line to one of its candidate products (and optionally learn it)."""
    settings = settings or get_settings()

    def apply(ln: PlanLineRow) -> None:
        for alt in ln.alternatives:
            if alt.get("sku") == sku:
                ln.selected_sku = alt["sku"]
                ln.selected_name = alt.get("name")
                ln.selected_brand = alt.get("brand")
                ln.selected_size = alt.get("size")
                ln.selected_url = alt.get("url")
                ln.selected_price_cents = alt.get("price_cents")
                if learn:
                    from app.match import set_alias
                    set_alias(ln.need, sku, alt.get("name"), settings)
                return

    _mutate_line(plan_id, line_id, apply, settings)


def approve(plan_id: int, settings: Settings | None = None) -> PlanRow | None:
    """Mark a plan approved. (Cart hand-off to FreshDirect is a later step.)"""
    settings = settings or get_settings()
    with session_scope(settings) as db:
        plan = db.get(PlanRow, plan_id)
        if plan is None:
            return None
        plan.status = "approved"
        db.add(plan)
        db.commit()
        db.refresh(plan)
        plan.lines
        db.expunge_all()
        return plan


def _mutate_line(plan_id: int, line_id: int, fn, settings: Settings | None) -> None:
    with session_scope(settings or get_settings()) as db:
        line = db.get(PlanLineRow, line_id)
        if line is None or line.plan_id != plan_id:
            return
        fn(line)
        db.add(line)
        db.commit()
