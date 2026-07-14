"""MCP server: FreshDirect auth (session status) + the read-only data pipeline.

Exposes history, backfill, spend/replenishment analytics, live catalog search,
SKU matching, and dietary-profile inference as MCP tools. Planning, cart
hand-off, and checkout are deliberately out of scope here — see `app/cli.py`
(`fdplanner plan` / `fdplanner serve`) for those.

Interactive login opens a real, headed Chrome window for 2FA/captcha and can't
be driven from a synchronous MCP tool call: run `uv run fdplanner login` in a
terminal, then use `fd_session_status` to confirm the saved session is there.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.freshdirect.base import Order, Product, SessionExpired

mcp = FastMCP("freshdirect-planner")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _order_dict(order: Order) -> dict:
    return _jsonable(order.model_dump())


def _product_dict(product: Product) -> dict:
    return _jsonable(product.model_dump())


@mcp.tool()
def fd_session_status() -> dict:
    """Check whether a FreshDirect session is saved locally.

    Does not open a browser. If none is saved (or a data tool below reports it
    has expired), run `uv run fdplanner login` in a terminal — that opens a real
    Chrome window for interactive login (handles 2FA/captcha), which can't be
    driven from an MCP tool call.
    """
    settings = get_settings()
    from app.freshdirect.session import has_session

    exists = has_session(settings)
    return {
        "session_saved": exists,
        "chrome_profile_dir": str(settings.chrome_profile_dir),
        "instructions": None
        if exists
        else "No saved session. Run `uv run fdplanner login` in a terminal to log in.",
    }


@mcp.tool()
async def fd_history(limit: int = 10, with_details: bool = False) -> dict:
    """Read recent FreshDirect orders from the saved session via GraphQL.

    Set with_details=True to also fetch each order's line items (slower: one
    extra page load per order). Requires a saved session (see fd_session_status).
    """
    from app.freshdirect.client import fetch_order_history

    try:
        # Playwright's sync API refuses to run on a thread with an active
        # asyncio loop (which is exactly the thread FastMCP calls us on), so
        # push the actual browser work onto a worker thread.
        orders = await asyncio.to_thread(
            fetch_order_history, get_settings(), limit=limit, with_details=with_details
        )
    except SessionExpired as exc:
        return {"error": str(exc)}
    return {"orders": [_order_dict(o) for o in orders]}


@mcp.tool()
async def fd_backfill(limit: int = 500) -> dict:
    """Sync full order history (with line items) into the local database.

    Resumable: re-running only fetches orders whose line items aren't stored
    yet. This is the step fd_spend, fd_due, and fd_profile read from. Requires
    a saved session.
    """
    from app.ingest import backfill as run_backfill

    try:
        result = await asyncio.to_thread(run_backfill, get_settings(), limit=limit)
    except SessionExpired as exc:
        return {"error": str(exc)}
    return {
        "orders_seen": result.orders_seen,
        "details_fetched": result.details_fetched,
        "skipped": result.skipped,
    }


@mcp.tool()
def fd_spend() -> dict:
    """Spend totals, monthly trend, and category breakdown from the local DB.

    Reads only the local database populated by fd_backfill — no browser.
    """
    from app.analytics import spend_summary
    from app.money import dollars

    s = spend_summary(get_settings())
    return {
        "order_count": s.order_count,
        "total": dollars(s.total_cents),
        "avg_order": dollars(s.avg_order_cents),
        "first_date": s.first_date.isoformat() if s.first_date else None,
        "last_date": s.last_date.isoformat() if s.last_date else None,
        "by_month": [
            {"month": ym, "total": dollars(cents), "orders": count}
            for ym, cents, count in s.by_month
        ],
        "by_category": [
            {"category": cat, "total": dollars(cents)} for cat, cents in s.by_category
        ],
    }


@mcp.tool()
def fd_due(
    limit: int = 25,
    min_purchases: int = 3,
    horizon_days: int = 7,
    include_inactive: bool = False,
) -> dict:
    """Staples predicted due for restock, most overdue first, from the local DB.

    By default returns active items due within horizon_days. Set
    include_inactive=True to also include items that fell out of rotation.
    Reads only the local database — no browser.
    """
    from app.analytics import replenishment

    preds = replenishment(get_settings(), min_purchases=min_purchases)
    if not include_inactive:
        preds = [p for p in preds if p.active and p.days_overdue >= -horizon_days]
    return {
        "items": [
            {
                "name": p.name,
                "times_bought": p.times_bought,
                "mean_interval_days": p.mean_interval_days,
                "last_purchased": p.last_purchased.isoformat(),
                "predicted_next": p.predicted_next.isoformat(),
                "days_overdue": p.days_overdue,
                "active": p.active,
            }
            for p in preds[:limit]
        ]
    }


@mcp.tool()
async def fd_search(query: str, limit: int = 12) -> dict:
    """Search the live FreshDirect catalog and return candidate products with prices.

    Requires a saved session.
    """
    from app.freshdirect.client import FreshDirectClient

    try:
        products = await asyncio.to_thread(
            FreshDirectClient(get_settings()).search_products, query, limit=limit
        )
    except SessionExpired as exc:
        return {"error": str(exc)}
    return {"products": [_product_dict(p) for p in products]}


@mcp.tool()
async def fd_match(item: str, brand: str | None = None) -> dict:
    """Resolve a free-text grocery need to a real FreshDirect SKU.

    Tries a learned alias first, then heuristic ranking of live search results
    (plus an optional Claude re-rank if FDPLANNER_ANTHROPIC_API_KEY is set).
    Requires a saved session (live search). Wrong pick? Correct it with
    fd_teach_match so future calls resolve instantly.
    """
    from app.freshdirect.client import FreshDirectClient
    from app.match import resolve

    settings = get_settings()
    try:
        candidates = await asyncio.to_thread(
            FreshDirectClient(settings).search_products, item, limit=30
        )
    except SessionExpired as exc:
        return {"error": str(exc)}

    result = resolve(item, candidates, preferred_brand=brand, settings=settings)
    if result.pick is None:
        return {"query": item, "pick": None, "needs_review": True}
    return {
        "query": item,
        "pick": _product_dict(result.pick),
        "confidence": result.confidence,
        "method": result.method,
        "needs_review": result.needs_review,
        "alternatives": [_product_dict(p) for p in result.alternatives],
    }


@mcp.tool()
def fd_teach_match(item: str, sku: str) -> dict:
    """Record a corrected SKU for a free-text need so future fd_match calls resolve it instantly."""
    from app.match import set_alias

    set_alias(item, sku, settings=get_settings())
    return {"learned": True, "query": item, "sku": sku}


@mcp.tool()
def fd_profile(use_ai: bool = True) -> dict:
    """Infer (and save) a household dietary profile from purchase history.

    Works fully offline from the local DB; if FDPLANNER_ANTHROPIC_API_KEY is set
    also refines the draft with Claude. Saved to data/profile.json.
    """
    from app.profile import infer_profile

    profile, signals = infer_profile(get_settings(), use_ai=use_ai)
    if signals.total_items == 0:
        return {"error": "No items yet — run fd_backfill first."}
    profile.save(get_settings())
    return profile.model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
