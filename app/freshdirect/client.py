"""Live FreshDirect access: drive real Chrome and read its GraphQL responses.

:class:`FreshDirectClient` owns one booted SPA session and exposes the two reads
the planner needs — the order list and a single order's line items — by
navigating the React routes and intercepting the GraphQL responses
(``ordersHistory`` / ``order``). Booting the SPA once and reusing the page makes
a full backfill (one detail page per order) practical, and the same session
abstraction is what later phases will use to build a cart.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

from app.config import Settings
from app.freshdirect.base import Order, OrderItem, Product, SessionExpired
from app.freshdirect.parse import (
    GraphQLSink,
    parse_cart_line,
    parse_order_summary,
    parse_product,
)
from app.freshdirect.session import browser_context, ensure_logged_in

# The header search box, matched by its placeholder (stable across the SPA).
_SEARCH_PLACEHOLDER = "What's on your shopping list?"


class _Session:
    """An open, booted SPA session bound to a single page."""

    def __init__(self, page, sink: GraphQLSink, settings: Settings) -> None:
        self._page = page
        self._sink = sink
        self._settings = settings

    def order_summaries(self, limit: int = 10) -> list[Order]:
        self._goto(self._settings.fd_account_url)
        history = self._wait_for("ordersHistory")
        if history is None:
            ensure_logged_in(self._page, self._settings)  # raises if expired
            raise RuntimeError(
                "Reached order history but never saw the ordersHistory GraphQL "
                "response — the API may have changed."
            )
        infos = (history or {}).get("ordersInfo") or []
        return [parse_order_summary(info) for info in infos[:limit]]

    def order_lines(self, order_id: str) -> list[OrderItem]:
        self._sink.pop("order")
        self._goto(f"{self._settings.fd_base_url}/account/order_details/{order_id}")
        detail = self._wait_for("order")
        lines = (detail or {}).get("cartLines") or []
        return [parse_cart_line(line) for line in lines if line]

    def search_products(self, query: str, limit: int = 30) -> list[Product]:
        """Search the catalog by driving the header search box like a user.

        Cold-navigating to the search URL doesn't bind the query (the SPA reads
        it from box state), so we type into the box and submit, then read the
        ``productSearch`` response whose ``text`` matches our query.
        """
        self._sink.pop("productSearch")
        box = self._page.get_by_placeholder(_SEARCH_PLACEHOLDER).first
        box.click()
        box.fill(query)
        box.press("Enter")

        result = self._wait_for_search(query)
        products = (result or {}).get("products") or []
        return [parse_product(p) for p in products[:limit] if p]

    # -- cart (mutating) --------------------------------------------------
    def cart_count(self) -> int | None:
        """Current number of cart lines, from the lightweightCart op (or None)."""
        lc = self._sink.ops.get("lightweightCart")
        return lc.get("cartLinesCount") if isinstance(lc, dict) else None

    def add_to_cart(self, product_url: str) -> bool:
        """Add one of a product to the live cart by clicking its 'Add to bag'.

        Adds quantity 1 only — deliberately. Multi-quantity is left to the human
        on the FreshDirect cart page; trying to drive a stepper from the product
        page risks mis-clicking a recommended product. Returns True once the page
        confirms the item is in the cart (its Remove control appears).
        """
        self._goto(product_url)
        add = self._page.get_by_role("button", name="Add to bag").first
        try:
            add.wait_for(state="visible", timeout=self._settings.nav_timeout_ms)
        except Exception:
            return False
        add.click()

        remove = self._page.get_by_role("button", name=re.compile(r"remove .* from cart", re.I))
        for _ in range(16):  # poll up to ~8s for the cart UI to update
            if remove.count():
                return True
            self._page.wait_for_timeout(500)
        return False

    def open_cart(self) -> str:
        """Return the cart/checkout URL (where the human finishes the order).

        ``/checkout`` redirects to FreshDirect's cart ('Your Bag'); we navigate
        there so the returned link lands the user on a populated cart.
        """
        try:
            self._page.goto(
                f"{self._settings.fd_base_url}/checkout",
                wait_until="domcontentloaded",
                timeout=self._settings.nav_timeout_ms,
            )
            self._page.wait_for_timeout(1500)
        except Exception:
            return f"{self._settings.fd_base_url}/checkout"
        return self._page.url

    def _wait_for_search(self, query: str):
        """Wait for a productSearch response that actually reflects this query."""
        want = query.strip().lower()
        for _ in range(max(1, self._settings.nav_timeout_ms // 500)):
            ps = self._sink.ops.get("productSearch")
            if isinstance(ps, dict):
                text = (ps.get("text") or "").strip().lower()
                # Accept once the bound query matches and results have settled.
                if text == want and ps.get("products") is not None:
                    return ps
            self._page.wait_for_timeout(500)
        return self._sink.ops.get("productSearch")

    # -- internals --------------------------------------------------------
    def _goto(self, url: str) -> None:
        self._page.goto(
            url, wait_until="domcontentloaded", timeout=self._settings.nav_timeout_ms
        )

    def _wait_for(self, op: str):
        for _ in range(max(1, self._settings.nav_timeout_ms // 500)):
            if op in self._sink.ops:
                return self._sink.ops[op]
            self._page.wait_for_timeout(500)
        return self._sink.ops.get(op)


class FreshDirectClient:
    """Adapter satisfying :class:`~app.freshdirect.base.FreshDirectAdapter`."""

    def __init__(self, settings: Settings, headed: bool = False) -> None:
        self.settings = settings
        self.headed = headed

    @contextmanager
    def session(self) -> Iterator[_Session]:
        """Open Chrome, boot the SPA, and yield a reusable session.

        Direct deep-links to the React routes render an error page, so we load
        the homepage first to boot the app before any account navigation.
        """
        with browser_context(self.settings, headless=not self.headed) as (_ctx, page):
            sink = GraphQLSink()
            page.on("response", sink.handle)
            page.goto(self.settings.fd_base_url, wait_until="domcontentloaded",
                      timeout=self.settings.nav_timeout_ms)
            page.wait_for_timeout(2000)
            yield _Session(page, sink, self.settings)

    def fetch_order_history(
        self, limit: int = 10, with_details: bool = False
    ) -> list[Order]:
        """Convenience one-shot: summaries, optionally enriched with line items."""
        with self.session() as s:
            orders = s.order_summaries(limit=limit)
            if with_details:
                for order in orders:
                    order.items = s.order_lines(order.order_id)
            return orders

    def search_products(self, query: str, limit: int = 30) -> list[Product]:
        """Convenience one-shot search (opens and closes a session)."""
        with self.session() as s:
            return s.search_products(query, limit=limit)


def fetch_order_history(
    settings: Settings,
    limit: int = 10,
    with_details: bool = False,
    headed: bool = False,
) -> list[Order]:
    """Module-level convenience used by the CLI."""
    return FreshDirectClient(settings, headed=headed).fetch_order_history(
        limit=limit, with_details=with_details
    )


__all__ = ["FreshDirectClient", "fetch_order_history", "SessionExpired"]
