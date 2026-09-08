"""The user agent has to name the platform Chrome is actually running on.

Chrome sends Sec-CH-UA-Platform from the real operating system, and nothing in
Playwright overrides it. A macOS UA string served from the Linux box is a
mismatch, which is the one thing driving real Chrome exists to avoid.
"""

from app.freshdirect.session import user_agent_for


def test_each_platform_gets_its_own_string():
    assert "Macintosh" in user_agent_for("Darwin")
    assert "X11; Linux x86_64" in user_agent_for("Linux")
    assert "Windows NT" in user_agent_for("Windows")


def test_an_unknown_platform_falls_back_rather_than_failing():
    assert user_agent_for("Plan9") == user_agent_for("Darwin")
