"""Config resolution: state dir must not depend on CWD or install location.

Regression guard for the packaging change — once the package is installed via
uv tool / uvx, app/ lives in site-packages and a repo-relative default would
split the CLI's session from the MCP server's. The default has to be a stable
per-user dir, overridable with FDPLANNER_DATA_DIR.
"""

import os
from pathlib import Path

from app.config import DEFAULT_DATA_DIR, Settings


def test_default_data_dir_is_cwd_independent(monkeypatch, tmp_path):
    """Same default no matter where the process was launched from."""
    monkeypatch.delenv("FDPLANNER_DATA_DIR", raising=False)

    monkeypatch.chdir(tmp_path)
    from_tmp = Settings().data_dir

    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.chdir(repo_root)
    from_repo = Settings().data_dir

    assert from_tmp == from_repo == DEFAULT_DATA_DIR


def test_default_is_not_under_the_package(monkeypatch):
    """The default must not sit next to app/ — that breaks once installed."""
    monkeypatch.delenv("FDPLANNER_DATA_DIR", raising=False)
    package_dir = Path(__file__).resolve().parent.parent / "app"
    assert package_dir not in DEFAULT_DATA_DIR.parents


def test_data_dir_env_override(monkeypatch, tmp_path):
    """FDPLANNER_DATA_DIR relocates all derived paths (DB, profile, digests)."""
    target = tmp_path / "state"
    monkeypatch.setenv("FDPLANNER_DATA_DIR", str(target))

    s = Settings()
    assert s.data_dir == target
    assert s.db_path == target / "fdplanner.sqlite"
    assert s.chrome_profile_dir == target / "chrome_profile"
    assert s.digests_dir == target / "digests"


def test_explicit_arg_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FDPLANNER_DATA_DIR", str(tmp_path / "from_env"))
    explicit = tmp_path / "explicit"
    assert Settings(data_dir=explicit).data_dir == explicit
