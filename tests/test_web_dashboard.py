"""The review dashboard's markup contract.

The phone layout and ``live.js`` both lean on things that are invisible in a
rendered page: region ids, per-row ids, ``data-live`` forms, and the ARIA roles
that stand in for table semantics once CSS overrides ``display`` on the cart.
A server-side refactor could drop any of them without anything looking wrong.

The geometry itself is checked in a browser (see DESIGN.md); this file covers
the parts that live in the template and the stylesheet.
"""

import re
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client over a throwaway data dir holding one two-line draft plan."""
    settings = Settings(data_dir=Path(tempfile.mkdtemp()))

    from app.db import init_db, session_scope
    from app.models import PlanLineRow, PlanRow

    init_db(settings)
    with session_scope(settings) as db:
        plan = PlanRow(
            week_of=date(2026, 9, 8),
            status="draft",
            budget_cap_cents=20_000,
            delivery_start=datetime(2026, 9, 13, 19, 0),
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        for need, price in [("Whole Milk", 429), ("Pasture Raised Eggs", 899)]:
            db.add(
                PlanLineRow(
                    plan_id=plan.id,
                    need=need,
                    reason="due for restock",
                    selected_sku="sku0",
                    selected_name=need,
                    selected_brand="Acme",
                    selected_price_cents=price,
                    alternatives=[
                        {"sku": "sku0", "name": need, "brand": "Acme",
                         "size": "1qt", "price_cents": price, "sold_out": False},
                        {"sku": "sku1", "name": need + " Large", "brand": "Acme",
                         "size": "2qt", "price_cents": price * 2, "sold_out": False},
                    ],
                )
            )
        db.commit()

    from app.web import server

    monkeypatch.setattr(server, "get_settings", lambda: settings)
    for mod in ("app.plans", "app.delivery", "app.inbox"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: settings, raising=False)
    server.app.dependency_overrides[server.get_settings] = lambda: settings
    from app.config import get_settings as real_get_settings

    server.app.dependency_overrides[real_get_settings] = lambda: settings
    try:
        yield TestClient(server.app)
    finally:
        server.app.dependency_overrides.clear()


def _plan_and_lines(client):
    html = client.get("/").text
    plan_id = int(re.search(r'action="/plan/(\d+)/line/', html).group(1))
    line_ids = [int(m) for m in re.findall(r'<tr id="line-(\d+)"', html)]
    assert line_ids, "the draft cart rendered no rows"
    return plan_id, line_ids


# --------------------------------------------------------------------------- #
# The markup the phone layout and live.js depend on
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("region", ["cart-body", "budget-region", "actions-region"])
def test_the_regions_live_js_swaps_are_present(client, region):
    """live.js replaces these three by id; a rename silently disables it."""
    assert f'id="{region}"' in client.get("/").text


def test_every_cart_row_is_addressable(client):
    html = client.get("/").text
    assert html.count('<tr id="line-') == html.count('/toggle" data-live')


def test_line_forms_are_marked_for_enhancement(client):
    html = client.get("/").text
    for action in ("/toggle", "/select", "/qty"):
        form = re.search(rf'<form[^>]*{re.escape(action)}"[^>]*>', html)
        assert form, f"no form posting to {action}"
        assert "data-live" in form.group(0)


def test_confirm_buttons_are_marked_js_optional(client):
    """They are the no-JS path; CSS hides them only once live.js has run."""
    html = client.get("/").text
    for label in (">swap<", ">set<"):
        button = re.search(rf'<button class="([^"]*)"[^>]*>\s*{re.escape(label[1:-1])}\s*<', html)
        assert button, f"no {label} button"
        assert "js-optional" in button.group(1)


def test_cart_carries_explicit_aria_roles(client):
    """The phone layout overrides `display` on the table, which drops the
    implicit roles; these spell them out so the semantics survive."""
    html = client.get("/").text
    for role in ('role="table"', 'role="rowgroup"', 'role="row"', 'role="cell"'):
        assert role in html


def test_the_toggle_reports_its_state(client):
    html = client.get("/").text
    assert 'data-state="in"' in html
    assert 'aria-pressed="true"' in html


def test_live_js_is_loaded_and_served(client):
    assert '/static/live.js' in client.get("/").text
    assert client.get("/static/live.js").status_code == 200


# --------------------------------------------------------------------------- #
# The stylesheet's mobile half
# --------------------------------------------------------------------------- #


def test_the_stylesheet_still_has_a_phone_layout():
    """The bug this replaced was a stylesheet with no breakpoint at all: a
    six-column cart rendered 775px wide inside a 390px viewport."""
    css = (STATIC / "style.css").read_text()
    assert "@media (min-width: 800px)" in css
    assert "grid-template-areas" in css, "the stacked row layout is gone"


def test_tap_targets_are_declared():
    css = (STATIC / "style.css").read_text()
    assert "--tap: 44px" in css
    assert re.search(r"\.toggle\s*\{[^}]*width:\s*var\(--tap\)", css, re.S)


def test_inputs_are_16px_on_phones():
    """Below 16px, iOS Safari zooms the viewport on focus — its own page jump."""
    css = (STATIC / "style.css").read_text()
    assert "--fs-md: 16px" in css
    assert re.search(r"input\[type=number\][^{]*\{[^}]*font-size:\s*var\(--fs-md\)", css, re.S)
