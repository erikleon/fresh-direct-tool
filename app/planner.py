"""Assemble a draft weekly plan from the replenishment forecast and the inbox.

Two sources feed a draft. The forecast supplies the staples predicted due; the
request inbox (:mod:`app.inbox`) supplies whatever somebody asked for out loud —
typically an item added to the shared Apple Reminders list. Both are priced
against the live catalog, the forecast preferring the *exact* product the
household has bought before (its historical ``product_id``) and the requests
resolved from free text, then the whole thing is reconciled against the weekly
budget with cheaper-swap suggestions. The result is a reviewable draft cart.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import ai
from decimal import Decimal
from typing import Callable

from app.analytics import Prediction, replenishment
from app.budget import DraftPlan, PlanLine, reconcile
from app.config import Settings, get_settings
from app.freshdirect.base import Product
from app.freshdirect.client import FreshDirectClient
from app.match import calibrated_confidence, normalize_key, resolve, score_candidates
from app.models import RequestRow
from app import pantry

ProgressCb = Callable[[str], None]


@ai.planning_session()
def build_plan(
    settings: Settings | None = None,
    horizon_days: int = 7,
    max_items: int = 25,
    budget: float | None = None,
    headed: bool = False,
    progress: ProgressCb | None = None,
) -> DraftPlan:
    """Build a draft plan: items due within ``horizon_days``, plus open requests.

    ``max_items`` caps the forecast only. A request is something a person typed,
    so it is never dropped to make room for a predicted staple.
    """
    settings = settings or get_settings()
    say = progress or (lambda _m: None)

    cap = budget if budget is not None else settings.weekly_budget
    cap_cents = int(round(cap * 100)) if cap else None

    forecast = replenishment(settings)
    due = [
        p for p in forecast
        if p.active and p.days_overdue >= -horizon_days
    ][:max_items]

    from app.inbox import list_open

    requested = list_open(settings)
    say(f"{len(due)} items due within {horizon_days}d, {len(requested)} requested. "
        "Pricing against the catalog…")

    plan = DraftPlan(week_of=date.today(), budget_cap_cents=cap_cents)
    from app.meals import suggest_meals, ingredient_query
    from app.delivery import next_preferred_delivery
    if settings.meal_count:
        try:
            local_today=datetime.now(ZoneInfo(settings.planning_timezone)).date()
        except (ZoneInfoNotFoundError, ValueError):
            plan.meal_context={'meals':[], 'notes':[
                'Planning timezone is unavailable; meal suggestions were skipped. Staples and shopping requests are still included.']}
        else:
            start=next_preferred_delivery(after=local_today,day=settings.preferred_delivery_day,hour=settings.preferred_delivery_after_hour).date()+timedelta(days=1)
            say("Choosing house recipes with the forecast after the next delivery…")
            plan.meal_context=suggest_meals(settings,start,min(7,max(1,horizon_days)),due)
    client = FreshDirectClient(settings, headed=headed)

    with client.session() as fd:
        for i, pred in enumerate(due, 1):
            candidates = fd.search_products(pred.name, limit=20)
            line = _line_for(pred, candidates)
            if line is not None:
                plan.lines.append(line)
            say(f"  [{i}/{len(due)}] {pred.name}: "
                f"{'matched' if line and line.product.price else 'no price'}")

        # Requests go on after the forecast so the dedup below has the full set
        # of predicted staples to check against.
        planned_keys = {normalize_key(ln.need) for ln in plan.lines}
        for i, req in enumerate(requested, 1):
            key = normalize_key(req.text)
            if key in planned_keys:
                # Already coming as a staple. Consume the request anyway, or it
                # sits open forever and reappears on every later draft.
                plan.request_ids.append(req.id)
                say(f"  [{i}/{len(requested)}] {req.text}: already on the plan")
                continue

            candidates = fd.search_products(req.text, limit=20)
            line = _line_for_request(req, candidates, settings)
            if line is None:
                # Nothing in the catalog answered it. Leave the request open so
                # it shows up on the dashboard as unmet rather than vanishing.
                say(f"  [{i}/{len(requested)}] {req.text}: no match, left open")
                continue
            plan.lines.append(line)
            planned_keys.add(key)
            plan.request_ids.append(req.id)
            say(f"  [{i}/{len(requested)}] {req.text}: matched {line.product.name}")

        # Meal ingredients remain review-required: source recipe amounts are not
        # FreshDirect package counts and history does not establish pantry stock.
        meal_searches=0
        confirmed,pantry_note=pantry.load_confirmed(settings.pantry_file)
        if pantry_note and plan.meal_context.get('meals'):
            plan.meal_context.setdefault('notes',[]).append(pantry_note)
        bought={p.key:p for p in forecast}
        for meal in plan.meal_context.get('meals',[]):
            meal.setdefault('in_pantry',[]);meal.setdefault('likely_on_hand',[])
            for ingredient in meal['ingredients']:
                query=ingredient_query(ingredient)
                if query.lower() in ('water','salt','pepper','salt and pepper'):
                    continue  # Still visible in the full recipe ingredient checklist.
                if pantry.is_confirmed(query,confirmed):
                    meal['in_pantry'].append(ingredient);continue  # No search, no line.
                key=normalize_key(query)
                if key in planned_keys:
                    existing=next((line for line in plan.lines if normalize_key(line.need)==key),None)
                    if existing:
                        existing.confidence=min(existing.confidence,.4)
                        existing.reason+=f"; also needed for {meal['title']} ({meal['day']}): {ingredient}. Check combined quantity."
                    continue
                if meal_searches>=40:
                    meal['unmatched_ingredients'].append(ingredient);continue
                meal_searches+=1
                candidates=fd.search_products(query,limit=12)
                ranked=score_candidates(query,candidates)
                # Require overlap in the actual ingredient/product names, not
                # merely organic preference or incidental description tokens.
                query_terms=set(normalize_key(query).split()) - pantry.DESCRIPTORS
                ranked=[r for r in ranked if query_terms & set(normalize_key(r.product.name).split())]
                pick=next((r.product for r in ranked if not r.product.sold_out and r.product.price is not None),None)
                if pick is None:
                    meal['unmatched_ingredients'].append(ingredient);continue
                alternatives=[r.product for r in ranked if r.product.sku!=pick.sku][:4]
                reason=f"{meal['title']} ({meal['day']}): {ingredient}. Check pantry and package quantity."
                # An estimate only annotates: history cannot say how much is left.
                recent=next((bought[p.sku] for p in [pick,*alternatives]
                             if p.sku in bought and pantry.likely_on_hand(bought[p.sku])),None)
                if recent:
                    reason+=' '+pantry.on_hand_note(recent);meal['likely_on_hand'].append(ingredient)
                plan.lines.append(PlanLine(need=query,product=pick,quantity=1,source='meal',confidence=.4,included=False,
                    reason=reason,alternatives=alternatives))
                planned_keys.add(key)

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
        confidence = calibrated_confidence(top.score, runner)

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


def _line_for_request(
    req: RequestRow, candidates: list[Product], settings: Settings
) -> PlanLine | None:
    """Resolve a free-text request to a product line.

    Goes through the full :func:`app.match.resolve` — a learned alias first, then
    the heuristic, then local AI when enabled — because "oat milk" is
    exactly the ambiguous phrasing that alias learning exists for. A weak match
    still becomes a line: the household manager reviews the cart either way, and
    a flagged line they can correct is more useful than a silent omission.
    """
    if not candidates:
        return None

    result = resolve(req.text, candidates, settings=settings)
    if result.pick is None:
        return None

    where = "reminders list" if req.source == "reminders" else req.source
    return PlanLine(
        need=req.text,
        product=result.pick,
        quantity=1.0,
        source="manual",
        confidence=0.0 if result.force_review else result.confidence,
        reason=f"requested ({where}), matched by {result.method}" + ("; AI unavailable, heuristic fallback" if result.ai_unavailable else ""),
        alternatives=[a for a in result.alternatives if a.sku != result.pick.sku],
    )
