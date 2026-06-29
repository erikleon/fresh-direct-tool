"""Tests for the weekly email digest (pure render + no-SMTP fallback, no network)."""

import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.budget import DraftPlan, PlanLine
from app.config import Settings
from app.freshdirect.base import Product


@pytest.fixture
def settings() -> Settings:
    return Settings(data_dir=Path(tempfile.mkdtemp()), dashboard_url="http://host:8000")


def _p(sku, name, price, brand=None):
    return Product(sku=sku, name=name, brand=brand, price=Decimal(str(price)))


def _saved_plan(settings):
    from app.plans import get_plan, save_draft

    milk = PlanLine(need="organic whole milk",
                    product=_p("PICK", "Organic Whole Milk", 5.29, "Organic Valley"),
                    quantity=1, confidence=0.9)
    eggs = PlanLine(need="eggs", product=_p("EGG", "Large Eggs", 4.49),
                    quantity=2, confidence=0.3)  # low → needs review
    draft = DraftPlan(week_of=date(2026, 6, 1), lines=[milk, eggs], budget_cap_cents=2000)
    return get_plan(save_draft(draft, settings), settings)


def test_render_digest_has_summary_and_link(settings):
    from app.notify.email import render_digest

    plan = _saved_plan(settings)
    subject, text, html = render_digest(plan, settings)

    assert "week of 2026-06-01" in subject
    assert "2 items" in subject
    # subtotal 5.29 + 8.98 = 14.27, under the 20.00 cap
    assert "$14.27 of $20.00" in text and "under budget" in text
    assert "Organic Whole Milk" in text
    assert "1 line(s) flagged" in text  # eggs flagged for review
    url = f"http://host:8000/plan/{plan.id}"
    assert url in text and url in html
    assert "review" in html.lower()


def test_render_digest_nothing_due(settings):
    from app.notify.email import render_digest
    from app.plans import get_plan, save_draft

    plan = get_plan(save_draft(DraftPlan(week_of=date(2026, 6, 1), lines=[]), settings), settings)
    subject, text, _ = render_digest(plan, settings)
    assert "nothing due" in subject.lower()
    assert "Nothing is due" in text


def test_render_digest_includes_delivery(settings):
    from app.delivery import SavedAddress
    from app.notify.email import render_digest
    from app.plans import get_plan, set_delivery

    plan = _saved_plan(settings)
    addr = SavedAddress(id="2", address1="500 Example Ave", apartment=None,
                        city="Sample City", state="NY", zip_code="10002")
    set_delivery(plan.id, address=addr, delivery_date=date(2026, 6, 6),
                 tip_dollars=8.0, settings=settings)
    _, text, html = render_digest(get_plan(plan.id, settings), settings)
    assert "Sample City" in text and "tip $8.00" in text
    assert "Sample City" in html


def test_send_digest_falls_back_to_preview_file(settings):
    from app.notify.email import send_digest

    plan = _saved_plan(settings)
    result = send_digest(plan, settings)  # no SMTP configured
    assert result.sent is False
    assert result.preview_path is not None
    written = Path(result.preview_path)
    assert written.exists()
    assert "Review" in written.read_text(encoding="utf-8")
