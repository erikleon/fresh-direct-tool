"""Central configuration.

Settings load from environment variables (prefix ``FDPLANNER_``) and an optional
``.env`` file. Nothing here should ever be logged — it carries the encryption key
that protects the stored FreshDirect session.
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
    """Directory for the SQLite DB and the encrypted session blob."""

    # --- FreshDirect -----------------------------------------------------
    fd_base_url: str = "https://www.freshdirect.com"
    fd_account_url: str = "https://www.freshdirect.com/account/orders.jsp"
    """Order-history landing page. Verified/adjusted during the Phase 0 spike."""

    # --- Security --------------------------------------------------------
    # Fernet key (base64, 32 bytes) used to encrypt the Playwright storage_state.
    # If unset, app.security generates one and persists it to ``data/secret.key``
    # with 0600 perms (local-first convenience; set explicitly in production).
    encryption_key: str | None = None

    # --- Browser ---------------------------------------------------------
    headless: bool = True
    """Session *capture* always runs headed regardless; scrapes run per this flag."""
    nav_timeout_ms: int = 45_000

    # --- AI (used from Phase 2) -----------------------------------------
    anthropic_api_key: str | None = None
    planner_model: str = "claude-opus-4-8"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "storage_state.enc"

    @property
    def key_path(self) -> Path:
        return self.data_dir / "secret.key"

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
