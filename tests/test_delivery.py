"""Tests for delivery address parsing + set_delivery (no network)."""

import tempfile
from datetime import date
from pathlib import Path

import pytest

from app.config import Settings
from app.delivery import _parse_addresses


@pytest.fixture
def settings() -> Settings:
    return Settings(data_dir=Path(tempfile.mkdtemp()))


UDA = {
    "homeAddresses": [
        {"address": {"id": "1", "address1": "1 Test Plaza", "apartment": "Unit 2",
                     "city": "Anytown", "state": "NY", "zipCode": "10001"}},
        {"address": {"id": "2", "address1": "500 Example Ave", "apartment": None,
                     "city": "Sample City", "state": "NY", "zipCode": "10002"}},
    ],
    "corpAddresses": [],
    "selectedAddress": {"address": {"id": "2"}},
}


def test_parses_home_addresses_and_marks_selected():
    addrs = _parse_addresses(UDA)
    assert [a.id for a in addrs] == ["1", "2"]
    assert addrs[1].selected and not addrs[0].selected
    assert addrs[1].one_line() == "500 Example Ave, Sample City, NY, 10002"


def test_handles_empty_uda():
    assert _parse_addresses({}) == []


def test_next_preferred_delivery_is_target_evening():
    from app.delivery import next_preferred_delivery

    # From a Friday (2026-06-05), the next Sunday after 7pm is 2026-06-07 19:00.
    dt = next_preferred_delivery(after=date(2026, 6, 5), day="sun", hour=19)
    assert dt.weekday() == 6  # Sunday
    assert dt.date() >= date(2026, 6, 5) and (dt.date() - date(2026, 6, 5)).days < 7
    assert dt.hour == 19 and dt.minute == 0
    # Same weekday as `after` resolves to that day, not a week later.
    same = next_preferred_delivery(after=date(2026, 6, 7), day="sun", hour=19)
    assert same.date() == date(2026, 6, 7)


def test_save_draft_suggests_sunday_evening(settings):
    from app.budget import DraftPlan, PlanLine
    from app.freshdirect.base import Product
    from app.plans import get_plan, save_draft

    pid = save_draft(
        DraftPlan(week_of=date(2026, 6, 1),
                  lines=[PlanLine(need="milk", product=Product(sku="X", name="Milk"))]),
        settings,
    )
    plan = get_plan(pid, settings)
    assert plan.delivery_start.weekday() == 6  # Sunday (config default)
    assert plan.delivery_start.hour == 19      # after 7pm
    assert plan.delivery_when().endswith("after 7:00 PM")


def test_set_delivery_persists(settings):
    from app.budget import DraftPlan, PlanLine
    from app.delivery import SavedAddress
    from app.freshdirect.base import Product
    from app.plans import get_plan, save_draft, set_delivery

    pid = save_draft(
        DraftPlan(week_of=date(2026, 6, 1),
                  lines=[PlanLine(need="milk", product=Product(sku="X", name="Milk"))]),
        settings,
    )
    addr = SavedAddress(id="2", address1="500 Example Ave", apartment=None,
                        city="Sample City", state="NY", zip_code="10002")
    set_delivery(pid, address=addr, delivery_date=date(2026, 6, 6), tip_dollars=8.0, settings=settings)

    plan = get_plan(pid, settings)
    assert plan.city == "Sample City"
    assert plan.tip_cents == 800
    assert plan.delivery_start.date() == date(2026, 6, 6)
    assert plan.delivery_start.hour == 19  # ideal time kept when the date changes
    assert "Sample City" in plan.address_one_line()
