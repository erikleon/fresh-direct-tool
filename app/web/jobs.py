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


_state = JobState()
_lock = threading.Lock()


def get_state() -> JobState:
    return _state


def is_running() -> bool:
    return _state.status == "running"


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
