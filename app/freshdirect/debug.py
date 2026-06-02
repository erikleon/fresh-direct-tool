"""Developer tooling for tuning scraper selectors against the live site.

Not part of the product flow — this exists so we can capture the *real*
FreshDirect order-history DOM (HTML + screenshot) using the saved session and
adjust the selectors in :mod:`app.freshdirect.history` to match. Touches the
user's account read-only (navigation only; no cart changes).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.freshdirect.history import _SEL
from app.freshdirect.session import browser_context


@dataclass
class DumpResult:
    final_url: str
    title: str
    html_path: Path
    screenshot_path: Path
    selector_hits: dict[str, int]
    looks_logged_out: bool


def dump_orders_page(
    settings: Settings, url: str | None = None, headed: bool = False
) -> DumpResult:
    """Navigate to the orders page and capture HTML + a full-page screenshot.

    Reports how many elements each current selector matches so we can see at a
    glance which ones are wrong, and saves artifacts under ``data/`` for review.
    ``headed=True`` runs visibly, which can clear a bot challenge headless trips.
    """
    settings.ensure_dirs()
    target = url or settings.fd_account_url
    html_path = settings.data_dir / "orders_page.html"
    shot_path = settings.data_dir / "orders_page.png"

    with browser_context(settings, headless=not headed) as (_context, page):
        page.goto(target, wait_until="domcontentloaded", timeout=settings.nav_timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=settings.nav_timeout_ms)
        except Exception:
            pass  # networkidle can never settle on chatty pages; proceed anyway

        # Nudge lazy-loaded order cards into the DOM.
        for _ in range(4):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(600)

        final_url = page.url
        title = page.title()
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(shot_path), full_page=True)

        hits = {name: page.locator(sel).count() for name, sel in _SEL.items()}
        looks_logged_out = any(
            t in final_url.lower()
            for t in ("login", "signin", "sign-in", "registration")
        )

    return DumpResult(
        final_url=final_url,
        title=title,
        html_path=html_path,
        screenshot_path=shot_path,
        selector_hits=hits,
        looks_logged_out=looks_logged_out,
    )
