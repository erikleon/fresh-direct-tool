"""FreshDirect session lifecycle: log in once (headed), reuse many times.

FreshDirect fronts the site with Akamai Bot Manager, which blocks throwaway
automation (Playwright's bundled Chromium advertises ``navigator.webdriver`` and
automation flags → HTTP 403). To get through we drive **real Google Chrome** from
a dedicated, persistent profile with the automation tells removed, so the browser
looks like — and over time behaves like — a genuine human session.

Login may involve 2FA/captcha, which we don't automate: the user signs in once in
the real Chrome window (clearing any challenge as a human), and the resulting
cookies persist in the profile for later headless scrapes.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.parse
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, sync_playwright

from app.config import Settings
from app.freshdirect.base import SessionExpired

logger = logging.getLogger(__name__)

# Removes the most common automation tells before any page script runs.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def _playwright_proxy() -> dict | None:
    """Build a Playwright proxy config from the environment, if set.

    Chrome on Windows reads proxy settings from WinInet, not env vars, so we
    read HTTPS_PROXY (or HTTP_PROXY) ourselves and forward it explicitly.  This
    ensures the browser reaches freshdirect.com when only env-var proxies are
    configured (common on corporate machines where the shell is set up by the
    user but Windows system proxy is not).
    """
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    if not raw:
        return None
    try:
        parsed = urllib.parse.urlparse(raw)
        server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        cfg: dict = {"server": server}
        if parsed.username:
            cfg["username"] = urllib.parse.unquote(parsed.username)
        if parsed.password:
            cfg["password"] = urllib.parse.unquote(parsed.password)
        bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
        if bypass:
            cfg["bypass"] = bypass
        return cfg
    except ValueError as exc:
        logger.debug("Ignoring unparseable proxy URL from environment: %s", exc, exc_info=True)
        return None


def has_session(settings: Settings) -> bool:
    """True once a profile has been created by a prior login."""
    profile = settings.chrome_profile_dir
    return profile.exists() and any(profile.iterdir())


@contextmanager
def _persistent_context(
    settings: Settings, headless: bool
) -> Iterator[tuple[BrowserContext, Page]]:
    """Launch real Chrome on the dedicated profile with stealth applied."""
    settings.ensure_dirs()
    settings.chrome_profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        proxy = _playwright_proxy()
        kwargs = dict(
            user_data_dir=str(settings.chrome_profile_dir),
            headless=headless,
            args=_LAUNCH_ARGS,
            user_agent=_USER_AGENT,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 900},
            **({"proxy": proxy} if proxy else {}),
        )
        try:
            context = p.chromium.launch_persistent_context(
                channel=settings.browser_channel, **kwargs
            )
        except PlaywrightError:
            # Real Chrome not found under the configured channel — fall back to
            # the bundled Chromium (more likely to be blocked, but better than
            # failing outright).
            context = p.chromium.launch_persistent_context(**kwargs)

        context.add_init_script(_STEALTH_JS)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(settings.nav_timeout_ms)
        try:
            yield context, page
        finally:
            context.close()


def capture_session(settings: Settings, timeout_s: int = 300) -> None:
    """Open a real Chrome window, let the user log in, then keep the profile.

    Completion is signalled by pressing Enter; if Enter never arrives (e.g. no
    interactive stdin) we stop after ``timeout_s`` so the call can't hang. The
    session lives in the persistent profile — there is nothing else to save.
    """
    with _persistent_context(settings, headless=False) as (_context, page):
        try:
            page.goto(settings.fd_base_url, timeout=settings.nav_timeout_ms)
        except PlaywrightError:
            pass  # let the user navigate manually even if the first load hiccups

        print(
            "\nA Chrome window has opened. Log into FreshDirect "
            "(complete any 2FA/captcha).\n"
            f"Press Enter here when done, or it finishes after {timeout_s}s."
        )
        _wait_for_user(timeout_s)

    print(f"Session captured. Profile: {settings.chrome_profile_dir}")


def _wait_for_user(timeout_s: int) -> None:
    """Block until the user presses Enter, or ``timeout_s`` elapses.

    Reading stdin on a background thread keeps the wait bounded: a
    non-interactive stdin raises ``EOFError`` and is ignored, so we fall through
    to the timeout rather than finishing prematurely.
    """
    done = threading.Event()

    def reader() -> None:
        try:
            input("Press Enter once you are logged in... ")
            done.set()
        except EOFError:
            pass  # no interactive stdin; rely on the timeout

    threading.Thread(target=reader, daemon=True).start()
    if not done.wait(timeout=timeout_s):
        print(f"\nNo Enter received in {timeout_s}s — continuing.")


@contextmanager
def browser_context(
    settings: Settings, headless: bool | None = None
) -> Iterator[tuple[BrowserContext, Page]]:
    """Yield an authenticated (context, page) from the saved Chrome profile.

    Raises :class:`SessionExpired` if no profile has been created yet. Whether
    the session is still *valid* is determined by the caller after navigation
    (see :func:`ensure_logged_in`).
    """
    if not has_session(settings):
        raise SessionExpired(
            "No saved FreshDirect session. Run `fdplanner login` first."
        )
    headless = settings.headless if headless is None else headless
    with _persistent_context(settings, headless=headless) as (context, page):
        yield context, page


def ensure_logged_in(page: Page, settings: Settings) -> None:
    """Heuristic login-wall check; raise :class:`SessionExpired` if logged out.

    FreshDirect bounces logged-out users to a sign-in URL. Selector specifics are
    confirmed during the Phase 0 spike and adjusted here if needed.
    """
    url = page.url.lower()
    if any(token in url for token in ("login", "signin", "sign-in", "registration")):
        raise SessionExpired(
            "Saved FreshDirect session has expired. Run `fdplanner login` again."
        )
