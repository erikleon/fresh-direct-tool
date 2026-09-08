"""Central configuration.

Settings load from environment variables (prefix ``FDPLANNER_``) and an optional
``.env`` file. The FreshDirect session itself lives in a persistent Chrome
profile (see ``chrome_profile_dir``), not here.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Local-first, but the state dir must NOT be derived from this file's location:
# once the package is installed (uv tool / uvx), app/ lives in site-packages and
# a repo-relative path would point at an ephemeral install dir the CLI and the
# MCP server don't share. Default to a stable per-user dir; override with
# FDPLANNER_DATA_DIR (e.g. to pin an existing ./data checkout).
DEFAULT_DATA_DIR = Path(user_data_dir("freshdirect-planner", appauthor=False))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FDPLANNER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage ---------------------------------------------------------
    data_dir: Path = Field(default=DEFAULT_DATA_DIR)
    """Directory for the SQLite DB and the persistent Chrome profile.

    Defaults to a per-user OS data dir so an installed server and the CLI agree
    regardless of working directory. Set FDPLANNER_DATA_DIR to relocate it.
    """

    # --- FreshDirect -----------------------------------------------------
    fd_base_url: str = "https://www.freshdirect.com"
    fd_account_url: str = "https://www.freshdirect.com/account/history"
    """Order-history page (a client-routed React view; boot the SPA first)."""

    # --- Browser ---------------------------------------------------------
    # We drive real Google Chrome (channel below) from a dedicated, persistent
    # profile so Akamai's bot defense sees a genuine, "warm" browser rather than
    # throwaway automation. The profile's cookies are encrypted at rest by the
    # OS keychain (Chrome Safe Storage), so no extra app-level crypto is needed.
    browser_channel: str = "chrome"
    headless: bool = True
    """Session *capture* always runs headed regardless; scrapes run per this flag."""
    nav_timeout_ms: int = 45_000

    # --- Planning --------------------------------------------------------
    weekly_budget: float | None = None
    """Weekly spend cap in dollars; None means no cap. Overridable per run."""

    # --- AI (used from Phase 2) -----------------------------------------
    anthropic_api_key: str | None = None
    anthropic_api_key_file: Path | None = None
    """Read the key from a file instead. Takes precedence over the variable.

    For running under Docker. ``GET /containers/{id}/json`` returns
    ``Config.Env``, so anything permitted to inspect a container reads every
    secret passed as an environment variable. A file is not handed out that way.
    """
    planner_model: str = "claude-opus-4-8"

    # --- Review surface (Phase 3) ---------------------------------------
    dashboard_url: str = "http://127.0.0.1:8000"
    """Base URL the email digest deep-links to (review/approve the draft)."""

    api_token_file: Path | None = None
    """Bearer token for /api/*, read from a file. Unset leaves the API closed.

    The HTML dashboard is unauthenticated and always has been: it is a
    local-first, single-household app. The JSON API is different, because it
    exists for Home Assistant to call across the box, so it carries a token.
    File rather than variable for the same reason as the Anthropic key above.
    """

    # --- Email digest (SMTP; all optional) ------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    digest_from: str | None = None
    """From address; falls back to smtp_user."""
    digest_to: str | None = None
    """Who receives the weekly digest. No value → no email is sent."""

    # --- Weekly scheduler (Phase 3) -------------------------------------
    schedule_day: str = "sat"
    """Day-of-week cron field for the weekly draft run (mon…sun)."""
    schedule_hour: int = 7
    schedule_horizon_days: int = 7
    schedule_max_items: int = 25

    # --- Preferred delivery window --------------------------------------
    # The "ideal" delivery the planner suggests on each draft. FreshDirect
    # timeslots are reserved live at checkout, so this is a recorded preference
    # (prefilled + shown in the digest/dashboard), not a held slot.
    preferred_delivery_day: str = "sun"
    """Ideal delivery day-of-week (mon…sun)."""
    preferred_delivery_after_hour: int = 19
    """Earliest ideal delivery hour, 0–23 (19 = after 7pm)."""

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.digest_to)

    @property
    def resolved_anthropic_api_key(self) -> str | None:
        """The Anthropic key, from the file if one is configured."""
        return _read_secret_file(self.anthropic_api_key_file) or self.anthropic_api_key

    @property
    def api_token(self) -> str | None:
        """The bearer token for /api/*, or None when the API is closed."""
        return _read_secret_file(self.api_token_file)

    @property
    def digests_dir(self) -> Path:
        return self.data_dir / "digests"

    @property
    def chrome_profile_dir(self) -> Path:
        """Dedicated persistent Chrome profile holding the FreshDirect session."""
        return self.data_dir / "chrome_profile"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "fdplanner.sqlite"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def _read_secret_file(path: Path | None) -> str | None:
    """Read a secret from a file, or None if there is no usable file.

    Trailing whitespace is stripped, because a file written with an editor
    almost always ends in a newline and a newline inside a bearer token turns
    into a 401 that looks like a wrong token rather than a stray byte.

    A missing or unreadable file is not fatal here. The caller decides what an
    absent secret means: no key disables the AI paths, no token closes the API.
    """
    if path is None:
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("Cannot read secret file %s: %s", path, exc, exc_info=True)
        return None
    return value or None


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
