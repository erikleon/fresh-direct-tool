"""Developer tooling for tuning scraper selectors against the live site.

Not part of the product flow — this exists so we can capture the *real*
FreshDirect order-history DOM (HTML + screenshot) using the saved session and
adjust the selectors in :mod:`app.freshdirect.history` to match. Touches the
user's account read-only (navigation only; no cart changes).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.freshdirect.session import browser_context


@dataclass
class DumpResult:
    final_url: str
    title: str
    html_path: Path
    screenshot_path: Path
    looks_logged_out: bool


def dump_orders_page(
    settings: Settings, url: str | None = None, headed: bool = False
) -> DumpResult:
    """Boot the SPA, open a page, and capture its HTML + a full-page screenshot.

    A debugging aid for eyeballing what rendered. ``headed=True`` runs visibly,
    which can clear a bot challenge a headless reuse trips.
    """
    settings.ensure_dirs()
    target = url or settings.fd_account_url
    html_path = settings.data_dir / "orders_page.html"
    shot_path = settings.data_dir / "orders_page.png"

    with browser_context(settings, headless=not headed) as (_context, page):
        page.goto(settings.fd_base_url, wait_until="domcontentloaded",
                  timeout=settings.nav_timeout_ms)
        page.wait_for_timeout(2000)
        page.goto(target, wait_until="domcontentloaded", timeout=settings.nav_timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=settings.nav_timeout_ms)
        except Exception:
            pass  # networkidle can never settle on chatty pages; proceed anyway
        for _ in range(4):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(600)

        final_url = page.url
        title = page.title()
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(shot_path), full_page=True)
        looks_logged_out = any(
            t in final_url.lower()
            for t in ("login", "signin", "sign-in", "registration")
        )

    return DumpResult(
        final_url=final_url,
        title=title,
        html_path=html_path,
        screenshot_path=shot_path,
        looks_logged_out=looks_logged_out,
    )


# --------------------------------------------------------------------------- #
# Network capture — find the JSON API behind the React orders view
# --------------------------------------------------------------------------- #

_API_HINT = re.compile(r"(api|graphql|order|history|account|svc|service)", re.IGNORECASE)


@dataclass
class NetworkResult:
    final_url: str
    log_path: Path
    saved_bodies: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)


def capture_network(
    settings: Settings, url: str | None = None, headed: bool = False
) -> NetworkResult:
    """Boot the SPA, navigate to the orders view, and record XHR/fetch traffic.

    Direct deep-links to the React routes render an error page, so we load the
    homepage first to boot the app, then navigate to the orders URL. Every
    XHR/fetch is logged; JSON responses whose URL looks API-ish are saved to
    ``data/responses/`` so we can identify the order-history endpoint and read it
    directly instead of scraping hashed-classname DOM.
    """
    settings.ensure_dirs()
    target = url or settings.fd_account_url
    log_path = settings.data_dir / "network_log.json"
    bodies_dir = settings.data_dir / "responses"
    bodies_dir.mkdir(exist_ok=True)

    entries: list[dict] = []
    saved: list[str] = []

    with browser_context(settings, headless=not headed) as (_context, page):
        def on_response(response) -> None:
            req = response.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            ct = (response.headers or {}).get("content-type", "")
            entry = {
                "method": req.method,
                "url": response.url,
                "status": response.status,
                "content_type": ct,
            }
            entries.append(entry)
            if "json" in ct and _API_HINT.search(response.url):
                try:
                    body = response.json()
                except Exception:
                    return
                idx = len(saved)
                path = bodies_dir / f"{idx:02d}.json"
                path.write_text(json.dumps(body, indent=2)[:2_000_000], encoding="utf-8")
                saved.append(f"{path.name}  <-  {response.url}")

        page.on("response", on_response)

        # Boot the SPA, then client-navigate to the orders view.
        page.goto(settings.fd_base_url, wait_until="domcontentloaded",
                  timeout=settings.nav_timeout_ms)
        page.wait_for_timeout(2500)
        page.goto(target, wait_until="domcontentloaded", timeout=settings.nav_timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=settings.nav_timeout_ms)
        except Exception:
            pass
        for _ in range(4):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)

        final_url = page.url

    log_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    candidates = sorted({e["url"] for e in entries if _API_HINT.search(e["url"])})
    return NetworkResult(
        final_url=final_url,
        log_path=log_path,
        saved_bodies=saved,
        candidates=candidates,
    )
