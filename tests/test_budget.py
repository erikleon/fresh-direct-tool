"""Tests for the pure budget reconciliation (no network/DB/LLM)."""

from datetime import date
from decimal import Decimal

from app.budget import DraftPlan, PlanLine, reconcile
from app.freshdirect.base import Product


def _p(sku, price, sold_out=False):
    return Product(sku=sku, name=sku, price=Decimal(str(price)), sold_out=sold_out)


def _line(need, price, alts=(), qty=1.0):
    return PlanLine(
        need=need,
        product=_p(f"{need}-pick", price),
        quantity=qty,
        alternatives=[_p(f"{need}-alt{i}", p, so) for i, (p, so) in enumerate(alts)],
    )


def test_subtotal_sums_lines():
    plan = DraftPlan(week_of=date.today(), lines=[_line("a", 5.00), _line("b", 3.50, qty=2)])
    assert plan.subtotal_cents == 500 + 700


def test_no_cap_means_no_swaps():
    plan = DraftPlan(week_of=date.today(), lines=[_line("a", 99.00, alts=[(1.0, False)])])
    assert reconcile(plan) == []
    assert plan.over_by_cents == 0
    assert plan.lines[0].swap is None


def test_under_cap_no_swaps():
    plan = DraftPlan(week_of=date.today(), lines=[_line("a", 5.00)], budget_cap_cents=1000)
    assert reconcile(plan) == []


def test_over_cap_applies_biggest_saver_first():
    # subtotal 30, cap 20: swapping 'big' (20 -> 5) saves 15 and gets us under.
    plan = DraftPlan(
        week_of=date.today(),
        lines=[
            _line("big", 20.00, alts=[(5.00, False)]),
            _line("small", 10.00, alts=[(9.00, False)]),
        ],
        budget_cap_cents=2000,
    )
    applied = reconcile(plan)
    assert plan.lines[0].swap is not None      # big was swapped
    assert plan.lines[0].chosen.price == Decimal("5.00")
    assert plan.subtotal_cents == 500 + 1000   # 15.00, under cap
    assert applied[0].saves_cents == 1500       # biggest saver reported first
    assert plan.lines[1].swap is None           # small left alone (already under)


def test_cheapest_alternative_ignores_sold_out_and_pricier():
    line = _line("x", 10.00, alts=[(12.00, False), (4.00, True), (6.00, False)])
    assert line.cheapest_alternative().price == Decimal("6.00")  # 4.00 is sold out


def test_stays_over_when_no_swaps_help():
    plan = DraftPlan(week_of=date.today(), lines=[_line("a", 50.00)], budget_cap_cents=1000)
    reconcile(plan)
    assert plan.over_by_cents == 4000  # no alternatives → still over
