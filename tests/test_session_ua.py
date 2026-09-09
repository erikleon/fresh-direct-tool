"""The user agent has to name the platform Chrome is actually running on.

Chrome sends Sec-CH-UA-Platform from the real operating system, and nothing in
Playwright overrides it. A macOS UA string served from the Linux box is a
mismatch, which is the one thing driving real Chrome exists to avoid.
"""

import re

from app.freshdirect.session import chrome_major_version, user_agent_for


def test_each_platform_gets_its_own_string():
    assert "Macintosh" in user_agent_for("Darwin")
    assert "X11; Linux x86_64" in user_agent_for("Linux")
    assert "Windows NT" in user_agent_for("Windows")


def test_an_unknown_platform_falls_back_rather_than_failing():
    assert user_agent_for("Plan9") == user_agent_for("Darwin")


def test_the_version_in_the_ua_is_the_installed_chrome_version():
    """Sec-CH-UA carries the real major version, so a pinned one would disagree."""
    major = chrome_major_version()
    assert re.fullmatch(r"\d+", major)
    assert f"Chrome/{major}.0.0.0" in user_agent_for()


def test_no_platform_advertises_headless():
    for system in ("Darwin", "Linux", "Windows"):
        assert "Headless" not in user_agent_for(system)


# --------------------------------------------------------------------------- #
# Launch failures and profile locks
# --------------------------------------------------------------------------- #


def test_only_a_missing_browser_falls_back_to_bundled_chromium():
    """A locked profile is not a missing browser and must not be reported as one.

    The fallback used to swallow every launch error, so a profile another
    container still held surfaced as Playwright's "run `playwright install`"
    banner — pointing at a browser that was installed and working.
    """
    from playwright.sync_api import Error as PlaywrightError

    from app.freshdirect.session import _is_missing_browser

    missing = PlaywrightError(
        "Executable doesn't exist at /root/.cache/ms-playwright/chromium/chrome"
    )
    locked = PlaywrightError(
        "Timeout 180000ms exceeded.\n  - [err] The profile appears to be in use "
        "by another Google Chrome process (40) on another computer (5ebff2905cf8)."
    )
    assert _is_missing_browser(missing) is True
    assert _is_missing_browser(locked) is False


def test_clear_stale_profile_lock_removes_a_dangling_symlink(tmp_path):
    """The lock is a symlink to a host that is gone, so it never resolves."""
    from app.config import Settings
    from app.freshdirect.session import clear_stale_profile_lock

    settings = Settings(data_dir=tmp_path)
    profile = settings.chrome_profile_dir
    profile.mkdir(parents=True)
    (profile / "SingletonLock").symlink_to("5ebff2905cf8-40")  # target never exists
    (profile / "Default").mkdir()

    assert clear_stale_profile_lock(settings) is True
    assert not (profile / "SingletonLock").is_symlink()
    assert (profile / "Default").exists()  # the profile itself is untouched
    assert clear_stale_profile_lock(settings) is False  # idempotent
