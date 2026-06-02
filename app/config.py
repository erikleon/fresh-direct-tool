"""Central configuration.

Settings load from environment variables (prefix ``FDPLANNER_``) and an optional
``.env`` file. The FreshDirect session itself lives in a persistent Chrome
profile (see ``chrome_profile_dir``), not here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (…/groceries). Local-first: all state lives under ./data by default.
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FDPLANNER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage ---------------------------------------------------------
    data_dir: Path = Field(default=ROOT / "data")
    """Directory for the SQLite DB and the persistent Chrome profile."""

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
    planner_model: str = "claude-opus-4-8"

    @property
    def chrome_profile_dir(self) -> Path:
        """Dedicated persistent Chrome profile holding the FreshDirect session."""
        return self.data_dir / "chrome_profile"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "fdplanner.sqlite"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
