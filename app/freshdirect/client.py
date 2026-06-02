"""Live FreshDirect access: drive real Chrome and read its GraphQL responses.

:class:`FreshDirectClient` owns one booted SPA session and exposes the two reads
the planner needs — the order list and a single order's line items — by
navigating the React routes and intercepting the GraphQL responses
(``ordersHistory`` / ``order``). Booting the SPA once and reusing the page makes
a full backfill (one detail page per order) practical, and the same session
abstraction is what later phases will use to build a cart.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.config import Settings
from app.freshdirect.base import Order, OrderItem, SessionExpired
from app.freshdirect.parse import GraphQLSink, parse_cart_line, parse_order_summary
from app.freshdirect.session import browser_context, ensure_logged_in


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
