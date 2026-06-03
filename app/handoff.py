"""Hand an approved plan off to the real FreshDirect cart.

The safe boundary: this *populates* the cart and returns a checkout link — it
never places the order or pays. The human does final checkout. Guards ensure we
only hand off an approved plan once, so we never double-add.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.config import Settings, get_settings
from app.db import session_scope
from app.freshdirect.client import FreshDirectClient
from app.models import PlanRow


@dataclass
class HandoffResult:
    ok: bool
    message: str = ""
    added: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    multi_qty: list[str] = field(default_factory=list)  # needs qty>1 set in the cart
    checkout_url: str | None = None
    cart_count_before: int | None = None
    cart_count_after: int | None = None


def handoff_plan(
    plan_id: int,
    settings: Settings | None = None,
    headed: bool = False,
    progress: Callable[[str], None] | None = None,
) -> HandoffResult:
    """Add an approved plan's included lines to the FreshDirect cart."""
    settings = settings or get_settings()
    say = progress or (lambda _m: None)

    # Snapshot the lines to add (and validate state) under a short DB session.
    with session_scope(settings) as db:
        plan = db.get(PlanRow, plan_id)
        if plan is None:
            return HandoffResult(ok=False, message="Plan not found.")
        if plan.status == "handed_off":
            return HandoffResult(ok=False, message="Already handed off.",
                                 checkout_url=plan.checkout_url)
        if plan.status != "approved":
            return HandoffResult(ok=False, message="Approve the plan before hand-off.")
        targets = [
            (ln.need, ln.selected_url, int(round(ln.quantity)))
            for ln in plan.lines
            if ln.included and ln.selected_url
        ]

    if not targets:
        return HandoffResult(ok=False, message="No items with a product link to add.")

    client = FreshDirectClient(settings, headed=headed)
    added: list[str] = []
    failed: list[str] = []
    multi_qty: list[str] = []
    checkout_url = None

    with client.session() as fd:
        before = fd.cart_count()
        say(f"Cart has {before} item(s). Adding {len(targets)}…")
        for i, (need, url, qty) in enumerate(targets, 1):
            try:
                ok = fd.add_to_cart(url)
            except Exception:
                ok = False
            (added if ok else failed).append(need)
            if ok and qty > 1:
                multi_qty.append(need)  # only qty 1 added; human sets the rest
            say(f"  [{i}/{len(targets)}] {need}: {'added' if ok else 'FAILED'}")
        after = fd.cart_count()
        checkout_url = fd.open_cart()

    # Persist hand-off outcome.
    with session_scope(settings) as db:
        plan = db.get(PlanRow, plan_id)
        if plan is not None:
            plan.status = "handed_off"
            plan.checkout_url = checkout_url
            db.add(plan)
            db.commit()

    return HandoffResult(
        ok=True,
        message=f"Added {len(added)} item(s)"
        + (f", {len(failed)} failed" if failed else "") + ".",
        added=added,
        failed=failed,
        multi_qty=multi_qty,
        checkout_url=checkout_url,
        cart_count_before=before,
        cart_count_after=after,
    )
