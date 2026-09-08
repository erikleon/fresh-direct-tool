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
