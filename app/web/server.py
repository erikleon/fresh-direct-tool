"""FastAPI app: the household manager's review-and-approve dashboard.

Server-rendered (Jinja) with plain form posts and redirect-after-post, so it
works without any client JS; the only dynamic bit is a meta-refresh while a plan
generates in the background.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.money import dollars, from_cents
from app.plans import (
    approve,
    get_plan,
    latest_plan_id,
    select_alternative,
    set_included,
    set_quantity,
)
from app.web import jobs

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))
templates.env.filters["dollars"] = dollars
templates.env.filters["cents"] = lambda c: f"{from_cents(c)}" if c is not None else "—"

app = FastAPI(title="FreshDirect Weekly Planner")
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


def _render(request: Request, plan):
    from app.delivery import load_addresses

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "plan": plan,
            "job": jobs.get_state(),
            "handoff": jobs.handoff_state(),
            "addresses": load_addresses(),
            "settings": get_settings(),
        },
    )


@app.get("/")
def home(request: Request):
    state = jobs.get_state()
    plan_id = state.plan_id if state.status == "done" else latest_plan_id()
    plan = get_plan(plan_id) if plan_id else None
    return _render(request, plan)


@app.post("/generate")
def generate(horizon: int = Form(7), budget: float = Form(None), max_items: int = Form(25)):
    jobs.start_generate(
        horizon_days=horizon, budget=budget or None, max_items=max_items
    )
    return RedirectResponse("/", status_code=303)


@app.get("/plan/{plan_id}")
def view_plan(request: Request, plan_id: int):
    return _render(request, get_plan(plan_id))


@app.post("/plan/{plan_id}/line/{line_id}/qty")
def edit_qty(plan_id: int, line_id: int, quantity: float = Form(...)):
    set_quantity(plan_id, line_id, quantity)
    return RedirectResponse(f"/plan/{plan_id}", status_code=303)


@app.post("/plan/{plan_id}/line/{line_id}/toggle")
def toggle_line(plan_id: int, line_id: int, included: str = Form("")):
    set_included(plan_id, line_id, included == "on")
    return RedirectResponse(f"/plan/{plan_id}", status_code=303)


@app.post("/plan/{plan_id}/line/{line_id}/select")
def select_line(plan_id: int, line_id: int, sku: str = Form(...)):
    select_alternative(plan_id, line_id, sku, learn=True)
    return RedirectResponse(f"/plan/{plan_id}", status_code=303)


@app.post("/plan/{plan_id}/delivery")
def edit_delivery(
    plan_id: int,
    address_id: str = Form(""),
    delivery_date: str = Form(""),
    tip: str = Form(""),
):
    from datetime import date as _date

    from app.delivery import load_addresses
    from app.plans import set_delivery

    address = next((a for a in load_addresses() if a.id == address_id), None)
    ddate = _date.fromisoformat(delivery_date) if delivery_date else None
    tip_val = float(tip) if tip else None
    set_delivery(plan_id, address=address, delivery_date=ddate, tip_dollars=tip_val)
    return RedirectResponse(f"/plan/{plan_id}", status_code=303)


@app.post("/plan/{plan_id}/approve")
def approve_plan(plan_id: int):
    approve(plan_id)
    return RedirectResponse(f"/plan/{plan_id}", status_code=303)


@app.post("/plan/{plan_id}/handoff")
def handoff(plan_id: int, confirm: str = Form("")):
    if confirm == "on":
        jobs.start_handoff(plan_id)
    return RedirectResponse(f"/plan/{plan_id}", status_code=303)


def included_subtotal(plan) -> int:
    return sum(ln.line_cents for ln in plan.lines if ln.included)


templates.env.globals["included_subtotal"] = included_subtotal
