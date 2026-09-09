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
import platform
import re
import shutil
import subprocess
import threading
import urllib.parse
from contextlib import contextmanager
from functools import lru_cache
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

# We have to send a user agent, because headless Chrome puts "HeadlessChrome" in
# its own and that is the plainest bot tell there is. What we send then has to
# agree with everything else the browser says about itself, in two ways that are
# easy to get wrong:
#
#   The platform. Chrome sends Sec-CH-UA-Platform from the real operating
#   system and nothing here can override it, so a macOS UA string from a Linux
#   server contradicts the client hints on every request.
#
#   The version. Sec-CH-UA carries the real major version, so a pinned number
#   in this file goes stale the first time Chrome updates and then disagrees.
#
# So the platform comes from the host and the version comes from the installed
# browser. _FALLBACK_MAJOR is only used when the binary cannot be asked.
_FALLBACK_MAJOR = "152"

_UA_TEMPLATES = {
    "Darwin": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    ),
    "Linux": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    ),
    "Windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    ),
}

_CHROME_BINARIES = {
    "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "Linux": ["google-chrome", "google-chrome-stable"],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
}


@lru_cache(maxsize=4)
def chrome_major_version(system: str | None = None) -> str:
    """Major version of the installed Chrome, or the pinned fallback.

    Windows Chrome does not answer ``--version`` on stdout, so that platform
    always takes the fallback. It is a development platform here, not the
    server, so the cost is a slightly stale number rather than a failure.
    """
    system = system or platform.system()
    for candidate in _CHROME_BINARIES.get(system, []):
        binary = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if not binary or not os.path.exists(binary):
            continue
        try:
            out = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Could not read Chrome's version from %s: %s", binary, exc, exc_info=True)
            continue
        match = re.search(r"\b(\d+)\.\d+\.\d+", out)
        if match:
            return match.group(1)

    logger.debug("Falling back to pinned Chrome major version %s", _FALLBACK_MAJOR)
    return _FALLBACK_MAJOR


def user_agent_for(system: str | None = None) -> str:
    """A UA naming this host's platform and this host's Chrome version."""
    system = system or platform.system()
    template = _UA_TEMPLATES.get(system, _UA_TEMPLATES["Darwin"])
    return template.format(major=chrome_major_version(system))


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


def _is_missing_browser(exc: BaseException) -> bool:
    """True when the launch failed because the browser is not installed.

    Playwright has no error type for this, so the message is all there is. The
    strings below are what it prints when an executable or a channel cannot be
    found; anything else is a browser that exists and did not start.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "executable doesn't exist",
            "executable does not exist",
            "looks like playwright was just installed",
            "please run the following command to download new browsers",
            "chrome distribution",
            "is not found at",
        )
    )


def clear_stale_profile_lock(settings: Settings) -> bool:
    """Remove a Chrome singleton lock left behind by a container that is gone.

    Chrome writes SingletonLock as a symlink naming the host and pid holding the
    profile, and removes it on a clean exit. A container killed mid-session
    leaves it, and the next start refuses the profile with "appears to be in use
    by another Google Chrome process on another computer" — naming a hostname
    that no longer exists anywhere.

    Only safe to call when nothing else is using the profile, which for a
    single-purpose container is true at startup. Returns True if it removed one.
    """
    profile = settings.chrome_profile_dir
    removed = False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = profile / name
        # A dangling symlink fails exists() but is still what blocks Chrome, so
        # test the link itself rather than its target.
        if path.is_symlink() or path.exists():
            try:
                path.unlink()
                removed = True
            except OSError as exc:
                logger.warning("Could not remove %s: %s", path, exc, exc_info=True)
    if removed:
        logger.info("Removed a stale Chrome profile lock from %s", profile)
    return removed


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
            user_agent=user_agent_for(),
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 900},
            **({"proxy": proxy} if proxy else {}),
        )
        try:
            context = p.chromium.launch_persistent_context(
                channel=settings.browser_channel, **kwargs
            )
        except PlaywrightError as exc:
            # Fall back to bundled Chromium ONLY when real Chrome is genuinely
            # absent. Anything else — a locked profile, a crash, a timeout — has
            # to surface as itself.
            #
            # This used to catch every PlaywrightError and retry. When the
            # second launch failed too, what reached the user was Playwright's
            # "run `playwright install`" banner, which points at a missing
            # browser. Measured against a profile another container still held:
            # Chrome was installed and working, the profile was locked, and the
            # error said neither.
            if not _is_missing_browser(exc):
                raise
            logger.warning(
                "Chrome channel %r not found; falling back to bundled Chromium, "
                "which this site is more likely to block. Original error: %s",
                settings.browser_channel, exc,
            )
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

    A stale lock from a previous run is cleared first. This is the one command
    where that is unambiguously safe: it is interactive, it is the only thing
    using the profile, and the alternative is a refusal that names a machine
    that no longer exists.
    """
    clear_stale_profile_lock(settings)
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
