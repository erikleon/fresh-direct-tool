"""Request inbox: idempotent capture, and the path from a request to a plan line.

The dedup rules carry real weight here. Home Assistant re-posts every open
reminder on each run, so ``external_id`` is what stops one reminder becoming a
new cart line every fifteen minutes.
"""

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


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #


def test_add_and_list_open(settings):
    from app.inbox import add_request, list_open

    row, created = add_request("oat milk", source="reminders", settings=settings)
    assert created and row.status == "open"
    assert [r.text for r in list_open(settings)] == ["oat milk"]


def test_external_id_makes_a_repost_a_no_op(settings):
    from app.inbox import add_request, list_open

    _, first = add_request("oat milk", external_id="uid-1", settings=settings)
    row, second = add_request("oat milk", external_id="uid-1", settings=settings)
    assert first is True and second is False
    assert len(list_open(settings)) == 1
    assert row.external_id == "uid-1"


def test_same_text_without_external_id_is_a_separate_request(settings):
    """Two people asking by hand is two requests; only an id proves sameness."""
    from app.inbox import add_request, list_open

    add_request("oat milk", settings=settings)
    add_request("oat milk", settings=settings)
    assert len(list_open(settings)) == 2


def test_reposting_a_planned_request_reopens_it(settings):
    """The reminder came back, so they want it again — the old cart is history."""
    from app.inbox import add_request, list_open, mark_planned

    row, _ = add_request("oat milk", external_id="uid-1", settings=settings)
    mark_planned([row.id], plan_id=1, settings=settings)
    assert list_open(settings) == []

    again, created = add_request("oat milk", external_id="uid-1", settings=settings)
    assert created is False
    assert again.status == "open" and again.plan_id is None
    assert len(list_open(settings)) == 1


def test_empty_text_and_unknown_source_are_refused(settings):
    from app.inbox import add_request

    with pytest.raises(ValueError):
        add_request("   ", settings=settings)
    with pytest.raises(ValueError):
        add_request("oat milk", source="carrier pigeon", settings=settings)


def test_drop_removes_it_from_open(settings):
    from app.inbox import add_request, drop, list_open

    row, _ = add_request("oat milk", settings=settings)
    assert drop(row.id, settings) is True
    assert list_open(settings) == []
    assert drop(9999, settings) is False


# --------------------------------------------------------------------------- #
# Requests reaching a plan
# --------------------------------------------------------------------------- #


def test_saving_a_draft_closes_the_requests_it_answered(settings):
    from app.inbox import add_request, list_open
    from app.plans import save_draft

    row, _ = add_request("oat milk", source="reminders", settings=settings)
    draft = DraftPlan(
        week_of=date(2026, 6, 1),
        lines=[PlanLine(need="oat milk", product=_p("OAT", "Oat Milk", 4.99), source="manual")],
        request_ids=[row.id],
    )
    plan_id = save_draft(draft, settings)

    assert list_open(settings) == []
    from app.db import session_scope
    from app.models import RequestRow

    with session_scope(settings) as db:
        stored = db.get(RequestRow, row.id)
        assert stored.status == "planned" and stored.plan_id == plan_id


def test_request_line_is_resolved_and_deduped_against_the_forecast(settings, monkeypatch):
    """A request already coming as a staple is consumed, not duplicated."""
    from app import planner
    from app.analytics import Prediction
    from app.inbox import add_request, list_open

    milk_req, _ = add_request("Whole Milk", source="reminders", settings=settings)
    oat_req, _ = add_request("oat milk", source="reminders", settings=settings)
    nothing_req, _ = add_request("saffron threads", source="reminders", settings=settings)

    catalog = {
        "whole milk": [_p("MILK", "Whole Milk", 5.29)],
        "oat milk": [_p("OAT", "Oatly Oat Milk", 4.99)],
        "saffron threads": [],
    }

    class _FD:
        def search_products(self, query, limit=20):
            return catalog.get(query.lower(), [])

    class _Client:
        def __init__(self, *a, **k):
            pass

        def session(self):
            from contextlib import contextmanager

            @contextmanager
            def _cm():
                yield _FD()

            return _cm()

    monkeypatch.setattr(planner, "FreshDirectClient", _Client)
    monkeypatch.setattr(
        planner,
        "replenishment",
        lambda _s: [
            Prediction(
                key="MILK", name="whole milk", times_bought=6, mean_interval_days=7.0,
                last_purchased=date(2026, 5, 25), days_since_last=8,
                predicted_next=date(2026, 6, 1), days_overdue=1, active=True,
            )
        ],
    )

    plan = planner.build_plan(settings)

    needs = [(ln.need, ln.source) for ln in plan.lines]
    assert ("whole milk", "replenish") in needs
    assert ("oat milk", "manual") in needs
    # "Whole Milk" was already on the plan as a staple, so it added no line …
    assert sum(1 for n, _ in needs if n.lower() == "whole milk") == 1
    # … but it was still consumed, or it would reappear on every future draft.
    assert set(plan.request_ids) == {milk_req.id, oat_req.id}

    # Nothing closes until the draft is saved, because that is where the plan id
    # comes from. Saffron matched nothing, so it stays open and visible.
    from app.plans import save_draft

    save_draft(plan, settings)
    assert [r.id for r in list_open(settings)] == [nothing_req.id]
