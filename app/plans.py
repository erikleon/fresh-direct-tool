"""Persistence + editing service for review-and-approve plans.

Bridges the in-memory :class:`~app.budget.DraftPlan` (how a plan is built) and the
:class:`~app.models.PlanRow` rows (how it's reviewed, edited, and approved). The
web layer calls these functions; they own all plan mutations.
"""

from __future__ import annotations

from typing import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.budget import DraftPlan
from app.config import Settings, get_settings
from app.db import init_db, session_scope
from app.freshdirect.base import Product
from app.models import PlanLineRow, PlanRow, PlanContextRow
from app.money import to_cents

from sqlmodel import select


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
    from app.delivery import next_preferred_delivery

    settings = settings or get_settings()
    try:
        local_today=datetime.now(ZoneInfo(settings.planning_timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        local_today=date.today()
    init_db(settings)
    with session_scope(settings) as db:
        plan = PlanRow(
            week_of=draft.week_of,
            budget_cap_cents=draft.budget_cap_cents,
            # Suggest the household's ideal slot (e.g. Sunday after 7pm); the
            # manager can change the date, and confirms the real slot at checkout.
            delivery_start=next_preferred_delivery(
                after=local_today,
                day=settings.preferred_delivery_day,
                hour=settings.preferred_delivery_after_hour,
            ),
        )
        for ln in draft.lines:
            chosen = ln.chosen
            plan.lines.append(
                PlanLineRow(
                    need=ln.need,
                    source=ln.source,
                    reason=ln.reason,
                    confidence=ln.confidence,
                    quantity=ln.quantity,
                    included=ln.included,
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
        db.flush()
        plan_id = plan.id
        # An older binary can delete a plan without knowing about this table.
        # SQLite may then reuse that plan ID: replace its context even when empty.
        context=db.get(PlanContextRow,plan_id) or PlanContextRow(plan_id=plan_id)
        context.payload=draft.meal_context
        db.add(context)
        db.commit()

    # Close the inbox requests this draft answered, now that there is an id to
    # point them at. Outside the session above: mark_planned opens its own.
    if draft.request_ids:
        from app.inbox import mark_planned

        mark_planned(draft.request_ids, plan_id, settings)
    return plan_id


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


def set_delivery(
    plan_id: int,
    address=None,
    delivery_date=None,
    tip_dollars=None,
    settings: Settings | None = None,
) -> None:
    """Record the plan's delivery address, preferred date, and tip."""
    from datetime import datetime, time
    from decimal import Decimal

    settings = settings or get_settings()
    with session_scope(settings) as db:
        plan = db.get(PlanRow, plan_id)
        if plan is None:
            return
        if address is not None:
            plan.address1 = address.address1
            plan.apartment = address.apartment
            plan.city = address.city
            plan.state = address.state
            plan.zip_code = address.zip_code
        if delivery_date is not None:
            if plan.delivery_start is None or plan.delivery_start.date()!=delivery_date:
                context=db.get(PlanContextRow,plan_id)
                if context and context.payload.get('meals'):
                    note='Delivery date changed. Meal dates and weather are from the original draft; regenerate the draft before relying on that schedule.'
                    context.payload={**context.payload,'stale':True,
                        'notes':list(dict.fromkeys([*context.payload.get('notes',[]),note]))}
                    db.add(context)
                    for line in plan.lines:
                        if line.source=='meal' or 'also needed for' in line.reason:
                            line.confidence=min(line.confidence,.4)
            # Keep the ideal time-of-day (e.g. after 7pm) when the date changes.
            plan.delivery_start = datetime.combine(
                delivery_date, time(settings.preferred_delivery_after_hour, 0)
            )
        if tip_dollars is not None:
            plan.tip_cents = to_cents(Decimal(str(tip_dollars)))
        db.add(plan)
        db.commit()


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


def discard(plan_id: int, settings: Settings | None = None) -> bool:
    """Delete a plan and its lines. Returns False if there was no such plan.

    Anything the household asked for that landed on this plan goes back to
    waiting, so discarding a draft loses the draft and not the asks — the
    requests are the part nobody can reconstruct. A handed-off plan is the one
    exception: those items are already in the real FreshDirect cart, so the
    asks were met and reopening them would put them on the next draft twice.
    """
    from app.models import RequestRow

    settings = settings or get_settings()
    with session_scope(settings) as db:
        plan = db.get(PlanRow, plan_id)
        if plan is None:
            return False
        fulfilled = plan.status == "handed_off"
        requests = db.exec(
            select(RequestRow).where(RequestRow.plan_id == plan_id)
        ).all()
        for row in requests:
            if fulfilled:
                row.plan_id = None  # keep it closed, just unhook the dead plan
            else:
                row.status = "open"
                row.plan_id = None
            db.add(row)
        context=db.get(PlanContextRow,plan_id)
        if context:
            db.delete(context)
        db.delete(plan)  # lines cascade; see PlanRow.lines
        db.commit()
        return True


def _mutate_line(plan_id: int, line_id: int, fn, settings: Settings | None) -> None:
    with session_scope(settings or get_settings()) as db:
        line = db.get(PlanLineRow, line_id)
        if line is None or line.plan_id != plan_id:
            return
        fn(line)
        db.add(line)
        db.commit()


def get_meal_context(plan_id, settings=None):
    init_db(settings)
    with session_scope(settings or get_settings()) as db:
        row=db.get(PlanContextRow,plan_id)
        if row is None or not row.payload:
            return {}
        from copy import deepcopy
        from datetime import datetime, timezone

        context=deepcopy(row.payload)
        has_weather=bool(context.get('forecast') or any(meal.get('weather') for meal in context.get('meals',[])))
        if not has_weather:
            return context
        stale=bool(context.get('stale'))
        try:
            generated=datetime.fromisoformat(context['generated_at'].replace('Z','+00:00'))
            age=(datetime.now(timezone.utc)-generated).total_seconds()
            stale=stale or not 0<=age<12*3600
        except (KeyError,TypeError,ValueError,AttributeError):
            stale=True
        if stale:
            context['weather_stale']=True
            context['forecast']=[]
            for meal in context.get('meals',[]):
                meal['weather']=None
                meal['reason']='Original recipe suggestion. Regenerate the draft for current weather and cooking advice.'
            note='Weather is stale or the delivery date changed. Regenerate the draft before relying on weather-based cooking suggestions.'
            context['notes']=list(dict.fromkeys([
                *(item for item in context.get('notes',[])
                  if not item.startswith('The forecast includes grilling weather,')),note]))
        return context
