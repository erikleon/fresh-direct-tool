"""JSON API under /api, for callers that are not a browser.

Home Assistant is the reason this exists: it watches a shared Apple Reminders
list and posts each item here, then reads back whether a draft is ready. It
cannot use the HTML forms the dashboard is built from, and it runs across the
box rather than on loopback next to a person.

So this half is authenticated and the HTML half is not. That is deliberate and
it is the boundary worth understanding: the dashboard has always been a
single-household local-first page with no login, protected by nothing reaching
it. These routes are reachable by anything on the box, so they carry a bearer
token from ``FDPLANNER_API_TOKEN_FILE``. With no token file configured the API
refuses everything rather than opening up.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.plans import get_plan, latest_plan_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


def require_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Reject anything without the configured bearer token.

    Compared with :func:`secrets.compare_digest` rather than ``==``. The token
    is low value and the box is not hostile, but a timing-safe comparison is one
    import and removes the question.
    """
    import secrets as _secrets

    expected = settings.api_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No API token is configured; set FDPLANNER_API_TOKEN_FILE.",
        )

    # Surrounding whitespace is stripped from what the caller presents, not just
    # from the token file. No token contains a leading or trailing space, so
    # tolerating them costs nothing and removes a whole class of paste error.
    # The one that actually happened: an iOS Shortcut header built as "Bearer "
    # plus a variable, giving two spaces, so the token arrived with a leading
    # space and was refused as wrong. Invisible in the editor, and the refusal
    # says "bad token", which sends you to check the token.
    scheme, _, presented = (authorization or "").partition(" ")
    presented = presented.strip()
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Expected an Authorization: Bearer header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _secrets.compare_digest(presented, expected):
        # Lengths only, never the values, and only to the server's own log: a
        # length mismatch is almost always a truncated paste, and a match with a
        # refusal is the wrong token rather than a mangled one.
        logger.warning(
            "Rejected an API token: presented %d characters, expected %d.",
            len(presented), len(expected),
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Bad token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class NewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    source: str = "reminders"
    external_id: str | None = Field(default=None, max_length=200)
    """Stable id at the source (a reminder's uid), so a repost is not a new line."""


@router.post("/requests", status_code=status.HTTP_201_CREATED)
def post_request(
    body: NewRequest,
    response: Response,
    _: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Add a free-text request to the inbox.

    201 when it was recorded, 200 when ``external_id`` had already been seen.
    The distinction is what lets the Home Assistant automation tell "sent" from
    "sent again", and it can retry a partial run without duplicating anything.
    """
    from app.inbox import add_request

    try:
        row, created = add_request(
            body.text, source=body.source, external_id=body.external_id, settings=settings
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if not created:
        response.status_code = status.HTTP_200_OK
    return {"id": row.id, "text": row.text, "created": created, "status": row.status}


@router.get("/requests")
def list_requests(
    _: None = Depends(require_token), settings: Settings = Depends(get_settings)
) -> dict:
    """Requests still waiting for a draft, oldest first."""
    from app.inbox import list_open

    rows = list_open(settings)
    return {
        "count": len(rows),
        "requests": [
            {
                "id": r.id,
                "text": r.text,
                "source": r.source,
                "external_id": r.external_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.delete("/requests/{request_id}")
def delete_request(
    request_id: int,
    _: None = Depends(require_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    from app.inbox import drop

    if not drop(request_id, settings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request.")
    return {"id": request_id, "status": "dropped"}


@router.post("/generate")
def post_generate(
    horizon_days: int = 7,
    max_items: int = 25,
    budget: float | None = None,
    _: None = Depends(require_token),
) -> dict:
    """Start a draft build. 409 if one is already running.

    This drives the live browser for a minute or two, so it returns as soon as
    the background job is accepted. Poll ``GET /api/plan/latest`` for the result.
    """
    from app.web import jobs

    started = jobs.start_generate(
        horizon_days=horizon_days, max_items=max_items, budget=budget
    )
    if not started:
        raise HTTPException(status.HTTP_409_CONFLICT, "A plan is already generating.")
    state = jobs.get_state()
    return {"started": True, "status": state.status, "message": state.message}


@router.get("/plan/latest")
def get_latest_plan(
    _: None = Depends(require_token), settings: Settings = Depends(get_settings)
) -> dict:
    """Enough of the newest plan to drive a sensor and a notification."""
    from app.web import jobs

    job = jobs.get_state()
    plan_id = latest_plan_id(settings)
    if plan_id is None:
        return {"plan": None, "job_status": job.status, "job_message": job.message}

    plan = get_plan(plan_id, settings)
    included = [ln for ln in plan.lines if ln.included]
    subtotal = sum(ln.line_cents for ln in included)
    return {
        "plan": {
            "id": plan.id,
            "week_of": plan.week_of.isoformat(),
            "status": plan.status,
            "lines": len(included),
            "requested_lines": sum(1 for ln in included if ln.source == "manual"),
            "needs_review": sum(1 for ln in included if ln.needs_review),
            "subtotal_cents": subtotal,
            "budget_cap_cents": plan.budget_cap_cents,
            "over_by_cents": max(0, subtotal - plan.budget_cap_cents)
            if plan.budget_cap_cents
            else 0,
        },
        "job_status": job.status,
        "job_message": job.message,
    }
