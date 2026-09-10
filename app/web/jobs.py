"""A tiny single-slot background job for plan generation.

Generating a plan drives the live browser for a minute or two, far too long to
block an HTTP request. We run it on a plain thread (no asyncio loop, so the
Playwright sync API is happy) and the dashboard polls this state via a
meta-refresh. Single-user, local-first: one job at a time is plenty.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class JobState:
    status: str = "idle"  # idle | running | done | error
    message: str = ""
    plan_id: int | None = None
    log: list[str] = field(default_factory=list)


@dataclass
class HandoffState:
    status: str = "idle"  # idle | running | done | error
    plan_id: int | None = None
    message: str = ""
    checkout_url: str | None = None
    added: int = 0
    failed: list[str] = field(default_factory=list)
    multi_qty: list[str] = field(default_factory=list)


_state = JobState()
_handoff = HandoffState()
_lock = threading.Lock()


def get_state() -> JobState:
    return _state


def handoff_state() -> HandoffState:
    return _handoff


def forget_plan(plan_id: int) -> None:
    """Drop a discarded plan from the job state.

    ``home()`` prefers the last generated plan id over the newest stored one, so
    without this the dashboard would keep pointing at a plan that no longer
    exists instead of falling back to whatever is actually there.
    """
    global _state, _handoff
    with _lock:
        if _state.plan_id == plan_id:
            _state = JobState()
        if _handoff.plan_id == plan_id:
            _handoff = HandoffState()


def is_running() -> bool:
    return _state.status == "running" or _handoff.status == "running"


def start_generate(**kwargs) -> bool:
    """Kick off generation in the background. Returns False if one is running."""
    global _state
    with _lock:
        if _state.status == "running":
            return False
        _state = JobState(status="running", message="Starting…")

    def run() -> None:
        from app.config import get_settings
        from app.plans import generate_and_save

        def progress(msg: str) -> None:
            _state.message = msg
            _state.log.append(msg)

        try:
            plan_id = generate_and_save(get_settings(), progress=progress, **kwargs)
            _state.plan_id = plan_id
            _state.status = "done"
            _state.message = "Draft ready."
        except Exception as exc:  # surface failure to the dashboard
            _state.status = "error"
            _state.message = f"{type(exc).__name__}: {exc}"

    threading.Thread(target=run, daemon=True).start()
    return True


def start_handoff(plan_id: int) -> bool:
    """Kick off cart hand-off in the background. False if one is running."""
    global _handoff
    with _lock:
        if _handoff.status == "running":
            return False
        _handoff = HandoffState(status="running", plan_id=plan_id, message="Adding to cart…")

    def run() -> None:
        from app.config import get_settings
        from app.handoff import handoff_plan

        try:
            res = handoff_plan(plan_id, get_settings(), progress=lambda m: setattr(_handoff, "message", m))
            _handoff.status = "done" if res.ok else "error"
            _handoff.message = res.message
            _handoff.checkout_url = res.checkout_url
            _handoff.added = len(res.added)
            _handoff.failed = res.failed
            _handoff.multi_qty = res.multi_qty
        except Exception as exc:
            _handoff.status = "error"
            _handoff.message = f"{type(exc).__name__}: {exc}"

    threading.Thread(target=run, daemon=True).start()
    return True
