"""The request inbox: free-text things somebody asked for.

The restock forecast covers staples the household buys on a cadence. It has
nothing to say about "we're out of oat milk", which is what a person actually
wants to add from the kitchen. Those arrive here — from a shared Apple Reminders
list through Home Assistant, from the dashboard, or from an MCP client — and
:mod:`app.planner` resolves each one to a real product when it builds the next
draft.

Same shape as :mod:`app.plans`: this module owns every mutation of the table.
(Named ``inbox`` rather than ``requests`` so it never shadows the HTTP library.)
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import select

from app.config import Settings, get_settings
from app.db import init_db, session_scope
from app.models import RequestRow

VALID_SOURCES = {"reminders", "manual", "mcp"}


def add_request(
    text: str,
    source: str = "manual",
    external_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[RequestRow, bool]:
    """Record a request. Returns ``(row, created)``.

    ``external_id`` makes this idempotent, which is what lets the Home Assistant
    automation retry a partial run safely: it posts every open reminder each
    time, and only the ones it has not sent before become new requests.

    A repost of something already ``planned`` reopens it, because a reminder
    that came back means they want it again — the previous cart is history.
    """
    text = text.strip()
    if not text:
        raise ValueError("request text is empty")
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {sorted(VALID_SOURCES)}")

    settings = settings or get_settings()
    init_db(settings)
    with session_scope(settings) as db:
        existing = None
        if external_id:
            existing = db.exec(
                select(RequestRow).where(RequestRow.external_id == external_id)
            ).first()
        if existing is not None:
            if existing.status != "open":
                existing.status = "open"
                existing.plan_id = None
                existing.created_at = datetime.utcnow()
                db.add(existing)
                db.commit()
                db.refresh(existing)
            db.expunge_all()
            return existing, False

        row = RequestRow(text=text, source=source, external_id=external_id)
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge_all()
        return row, True


def list_open(settings: Settings | None = None) -> list[RequestRow]:
    """Open requests, oldest first, so the longest-waiting item is planned first."""
    settings = settings or get_settings()
    init_db(settings)
    with session_scope(settings) as db:
        rows = db.exec(
            select(RequestRow)
            .where(RequestRow.status == "open")
            .order_by(RequestRow.created_at)
        ).all()
        db.expunge_all()
        return list(rows)


def mark_planned(
    request_ids: list[int], plan_id: int, settings: Settings | None = None
) -> None:
    """Attach requests to the plan they landed on, so they stop being open."""
    if not request_ids:
        return
    settings = settings or get_settings()
    with session_scope(settings) as db:
        for row in db.exec(select(RequestRow).where(RequestRow.id.in_(request_ids))).all():
            row.status = "planned"
            row.plan_id = plan_id
            db.add(row)
        db.commit()


def drop(request_id: int, settings: Settings | None = None) -> bool:
    """Withdraw a request. Returns False if there was no such open request."""
    settings = settings or get_settings()
    with session_scope(settings) as db:
        row = db.get(RequestRow, request_id)
        if row is None:
            return False
        row.status = "dropped"
        db.add(row)
        db.commit()
        return True
