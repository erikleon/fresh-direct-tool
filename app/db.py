"""SQLite engine + session helpers (local-first, one file under ``data/``)."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings
import app.models  # noqa: F401  — register tables on SQLModel.metadata


@lru_cache
def _engine_for(db_url: str):
    return create_engine(db_url, echo=False)


def get_engine(settings: Settings | None = None):
    settings = settings or get_settings()
    settings.ensure_dirs()
    return _engine_for(f"sqlite:///{settings.db_path}")


def init_db(settings: Settings | None = None) -> None:
    SQLModel.metadata.create_all(get_engine(settings))


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    with Session(get_engine(settings)) as session:
        yield session
