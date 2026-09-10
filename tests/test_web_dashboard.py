"""The review dashboard's markup contract and its redirect-after-post targets.

Two things this file protects, both invisible until somebody opens the page on
a phone:

* Editing one line sends the browser back to ``#line-<id>``. Without the
  fragment a plain redirect-after-post lands at scroll-top, so acting on the
  twelfth row throws you back to the header.
* The template still carries the hooks the phone layout and ``live.js`` need —
  region ids, per-row ids, ``data-live`` forms, and the ARIA roles that stand in
  for table semantics once CSS overrides ``display`` on the cart.

The geometry itself is checked in a browser (see DESIGN.md); these are the
parts a server-side refactor could quietly drop.
"""

import re
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select as _select

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


def _settings_of(client):
    """The throwaway Settings the fixture wired into the app."""
    from app.web import server

    return server.get_settings()


def _plan_and_lines(client):
    html = client.get("/").text
    plan_id = int(re.search(r'action="/plan/(\d+)/line/', html).group(1))
    line_ids = [int(m) for m in re.findall(r'<tr id="line-(\d+)"', html)]
    assert line_ids, "the draft cart rendered no rows"
    return plan_id, line_ids


# --------------------------------------------------------------------------- #
# Redirect-after-post lands on the row you edited
# --------------------------------------------------------------------------- #


def test_toggling_a_line_redirects_to_that_row(client):
    plan_id, line_ids = _plan_and_lines(client)
    line_id = line_ids[-1]
    res = client.post(
        f"/plan/{plan_id}/line/{line_id}/toggle",
        data={"included": "off"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == f"/plan/{plan_id}#line-{line_id}"


def test_setting_a_quantity_redirects_to_that_row(client):
    plan_id, line_ids = _plan_and_lines(client)
    line_id = line_ids[0]
    res = client.post(
        f"/plan/{plan_id}/line/{line_id}/qty",
        data={"quantity": "3"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == f"/plan/{plan_id}#line-{line_id}"


def test_swapping_a_product_redirects_to_that_row(client):
    plan_id, line_ids = _plan_and_lines(client)
    line_id = line_ids[0]
    res = client.post(
        f"/plan/{plan_id}/line/{line_id}/select",
        data={"sku": "sku1"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == f"/plan/{plan_id}#line-{line_id}"


def test_the_anchor_names_a_row_that_exists_on_the_page(client):
    """A fragment pointing at nothing is the same as no fragment at all."""
    plan_id, line_ids = _plan_and_lines(client)
    line_id = line_ids[-1]
    location = client.post(
        f"/plan/{plan_id}/line/{line_id}/toggle",
        data={"included": "off"},
        follow_redirects=False,
    ).headers["location"]
    anchor = location.split("#", 1)[1]
    assert f'<tr id="{anchor}"' in client.get(f"/plan/{plan_id}").text


def test_a_line_edit_still_applies_when_followed(client):
    """The fragment must not have broken the redirect the edit rides on."""
    plan_id, line_ids = _plan_and_lines(client)
    line_id = line_ids[0]
    res = client.post(
        f"/plan/{plan_id}/line/{line_id}/toggle", data={"included": "off"}
    )
    assert res.status_code == 200
    row = re.search(rf'<tr id="line-{line_id}"[^>]*class="([^"]*)"', res.text).group(1)
    assert "dropped" in row


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


# --------------------------------------------------------------------------- #
# Discarding a draft
# --------------------------------------------------------------------------- #


def test_discarding_removes_the_plan_and_its_lines(client):
    from app.models import PlanLineRow, PlanRow

    plan_id, _ = _plan_and_lines(client)
    res = client.post(f"/plan/{plan_id}/discard", data={"confirm": "on"})
    assert res.status_code == 200

    settings = _settings_of(client)
    from app.db import session_scope

    with session_scope(settings) as db:
        assert db.get(PlanRow, plan_id) is None
        left = db.exec(_select(PlanLineRow).where(PlanLineRow.plan_id == plan_id)).all()
        assert left == [], "plan lines outlived the plan"


def test_discarding_needs_the_confirm_box(client):
    """It is the one action here that destroys work, so a stray post must not."""
    from app.models import PlanRow

    plan_id, _ = _plan_and_lines(client)
    for data in ({}, {"confirm": ""}, {"confirm": "yes"}):
        client.post(f"/plan/{plan_id}/discard", data=data)

    from app.db import session_scope

    with session_scope(_settings_of(client)) as db:
        assert db.get(PlanRow, plan_id) is not None


def test_discarding_a_draft_puts_its_requests_back_in_the_queue(client):
    """The draft is reproducible; what somebody asked for is not."""
    from app.db import session_scope
    from app.inbox import mark_planned
    from app.models import RequestRow

    plan_id, _ = _plan_and_lines(client)
    settings = _settings_of(client)
    with session_scope(settings) as db:
        req = RequestRow(text="oat milk", source="reminders", status="open")
        db.add(req)
        db.commit()
        db.refresh(req)
        req_id = req.id
    mark_planned([req_id], plan_id, settings)

    client.post(f"/plan/{plan_id}/discard", data={"confirm": "on"})

    with session_scope(settings) as db:
        row = db.get(RequestRow, req_id)
        assert row.status == "open"
        assert row.plan_id is None
    assert "oat milk" in client.get("/").text


def test_discarding_a_handed_off_plan_does_not_reopen_its_requests(client):
    """Those items are in the real cart already; reopening would reorder them."""
    from app.db import session_scope
    from app.inbox import mark_planned
    from app.models import PlanRow, RequestRow

    plan_id, _ = _plan_and_lines(client)
    settings = _settings_of(client)
    with session_scope(settings) as db:
        req = RequestRow(text="paper towels", source="reminders", status="open")
        db.add(req)
        plan = db.get(PlanRow, plan_id)
        plan.status = "handed_off"
        db.add(plan)
        db.commit()
        db.refresh(req)
        req_id = req.id
    mark_planned([req_id], plan_id, settings)

    client.post(f"/plan/{plan_id}/discard", data={"confirm": "on"})

    with session_scope(settings) as db:
        row = db.get(RequestRow, req_id)
        assert row.status == "planned"
        assert row.plan_id is None, "the request still points at a deleted plan"


def test_the_dashboard_is_empty_after_discarding_the_only_plan(client):
    plan_id, _ = _plan_and_lines(client)
    html = client.post(f"/plan/{plan_id}/discard", data={"confirm": "on"}).text
    assert "No draft plan yet" in html
    assert '<tr id="line-' not in html


def test_discarding_falls_back_to_the_remaining_plan(client):
    """home() prefers the last generated id; a stale one must not blank the page."""
    from app.db import session_scope
    from app.models import PlanLineRow, PlanRow
    from app.web import jobs

    plan_id, _ = _plan_and_lines(client)
    settings = _settings_of(client)
    with session_scope(settings) as db:
        other = PlanRow(week_of=date(2026, 9, 1), status="draft")
        db.add(other)
        db.commit()
        db.refresh(other)
        db.add(PlanLineRow(plan_id=other.id, need="Rye Bread", selected_name="Rye Bread",
                           selected_price_cents=500, alternatives=[]))
        db.commit()
        other_id = other.id
    # Pretend the discarded plan was the one this session generated.
    jobs.get_state().plan_id = plan_id
    jobs.get_state().status = "done"
    try:
        html = client.post(f"/plan/{plan_id}/discard", data={"confirm": "on"}).text
        assert "Rye Bread" in html, "fell through to an empty page instead of the surviving plan"
    finally:
        jobs.forget_plan(other_id)
        jobs.forget_plan(plan_id)


def test_the_discard_control_is_offered_only_with_a_plan(client):
    assert "/discard" in client.get("/").text
    plan_id, _ = _plan_and_lines(client)
    assert "/discard" not in client.post(
        f"/plan/{plan_id}/discard", data={"confirm": "on"}
    ).text
