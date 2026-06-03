"""Tests for plan persistence + editing (isolated SQLite, no network/LLM)."""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.budget import DraftPlan, PlanLine
from app.config import Settings
from app.freshdirect.base import Product


@pytest.fixture
def settings() -> Settings:
    return Settings(data_dir=Path(tempfile.mkdtemp()))


def _p(sku, name, price, brand=None):
    return Product(sku=sku, name=name, brand=brand, price=Decimal(str(price)))


def _draft() -> DraftPlan:
    milk = PlanLine(
        need="organic whole milk",
        product=_p("PICK", "Organic Whole Milk", 5.29, "Organic Valley"),
        quantity=1,
        confidence=0.9,
        alternatives=[_p("CHEAP", "Organic Whole Milk", 3.99, "Store")],
    )
    eggs = PlanLine(
        need="eggs",
        product=_p("EGG", "Large Eggs", 4.49),
        quantity=2,
        confidence=0.3,  # low → needs review
    )
    return DraftPlan(week_of=date(2026, 6, 1), lines=[milk, eggs], budget_cap_cents=1000)


def test_save_and_load_roundtrip(settings):
    from app.plans import get_plan, save_draft

    pid = save_draft(_draft(), settings)
    plan = get_plan(pid, settings)
    assert plan is not None
    assert len(plan.lines) == 2
    assert plan.status == "draft"
    # 5.29*1 + 4.49*2 = 5.29 + 8.98 = 14.27
    assert sum(ln.line_cents for ln in plan.lines) == 529 + 898
    milk = next(ln for ln in plan.lines if ln.need == "organic whole milk")
    assert len(milk.alternatives) == 2  # original pick + the cheaper alt
    eggs = next(ln for ln in plan.lines if ln.need == "eggs")
    assert eggs.needs_review


def test_latest_plan_id(settings):
    from app.plans import latest_plan_id, save_draft

    assert latest_plan_id(settings) is None
    a = save_draft(_draft(), settings)
    b = save_draft(_draft(), settings)
    assert latest_plan_id(settings) == b and b != a


def test_edit_quantity_and_include(settings):
    from app.plans import get_plan, save_draft, set_included, set_quantity

    pid = save_draft(_draft(), settings)
    line_id = get_plan(pid, settings).lines[0].id
    set_quantity(pid, line_id, 3, settings)
    set_included(pid, line_id, False, settings)
    line = next(ln for ln in get_plan(pid, settings).lines if ln.id == line_id)
    assert line.quantity == 3
    assert line.included is False


def test_select_alternative_switches_and_learns(settings):
    from app.match import get_alias
    from app.plans import get_plan, save_draft, select_alternative

    pid = save_draft(_draft(), settings)
    milk = next(ln for ln in get_plan(pid, settings).lines if ln.need == "organic whole milk")
    select_alternative(pid, milk.id, "CHEAP", learn=True, settings=settings)
    updated = next(ln for ln in get_plan(pid, settings).lines if ln.id == milk.id)
    assert updated.selected_sku == "CHEAP"
    assert updated.selected_price_cents == 399
    assert updated.swapped
    assert get_alias("organic whole milk", settings) == "CHEAP"  # correction learned


def test_approve_sets_status(settings):
    from app.plans import approve, get_plan, save_draft

    pid = save_draft(_draft(), settings)
    approve(pid, settings)
    assert get_plan(pid, settings).status == "approved"
