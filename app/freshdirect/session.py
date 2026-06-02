"""FreshDirect session lifecycle: capture once (headed), reuse many times.

Login may involve 2FA or a captcha, which we don't try to automate. Instead the
user logs in once in a real browser window; we persist the resulting
``storage_state`` (encrypted) and reuse it for headless scrapes until it expires.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

from app.config import Settings
from app.freshdirect.base import SessionExpired
from app.security import decrypt, encrypt


def has_session(settings: Settings) -> bool:
    return settings.session_path.exists()


def capture_session(settings: Settings) -> None:
    """Open a real browser window, let the user log in, then save the session.

    Blocks on terminal input so the user can complete login (including 2FA /
    captcha) at their own pace before we snapshot the authenticated state.
    """
    settings.ensure_dirs()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(settings.fd_base_url, timeout=settings.nav_timeout_ms)

        print(
            "\nA browser window has opened. Log into FreshDirect "
            "(complete any 2FA/captcha),\nthen return here and press Enter to "
            "save the session."
        )
        input("Press Enter once you are logged in... ")

        state_json = context.storage_state()  # dict
        blob = encrypt(json.dumps(state_json).encode(), settings)
        settings.session_path.write_bytes(blob)
        os.chmod(settings.session_path, 0o600)
        context.close()
        browser.close()
    print(f"Session saved (encrypted) to {settings.session_path}")


@contextmanager
def browser_context(
    settings: Settings, headless: bool | None = None
) -> Iterator[tuple[BrowserContext, Page]]:
    """Yield an authenticated (context, page) built from the saved session.

    Raises :class:`SessionExpired` if no session has been captured yet. Whether
    the *current* session is still valid is determined by the caller after
    navigation (see :func:`ensure_logged_in`).
    """
    if not has_session(settings):
        raise SessionExpired(
            "No saved FreshDirect session. Run `fdplanner login` first."
        )

    state = decrypt(settings.session_path.read_bytes(), settings)
    headless = settings.headless if headless is None else headless

    # Playwright reads storage_state from a path at context creation; write it to
    # a temp file, then delete immediately so the decrypted session never lingers.
    tmp = Path(tempfile.mkstemp(suffix=".json")[1])
    try:
        tmp.write_bytes(state)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=str(tmp))
            tmp.unlink(missing_ok=True)  # decrypted copy no longer needed
            page = context.new_page()
            page.set_default_timeout(settings.nav_timeout_ms)
            try:
                yield context, page
            finally:
                context.close()
                browser.close()
    finally:
        tmp.unlink(missing_ok=True)


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
