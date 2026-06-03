"""The autonomous weekly run: build a draft, then email it for review.

``run_weekly`` is the whole job — generate a restock draft (driving the live
browser) and send the digest. It's reusable: the CLI ``run-weekly`` calls it once
now, and ``start_scheduler`` calls it on a weekly cron trigger (APScheduler, the
optional ``[scheduler]`` extra). The job never places an order; it stops at the
draft + notification, exactly like the manual flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.config import Settings, get_settings


@dataclass
class WeeklyResult:
    plan_id: int
    digest_detail: str
    digest_sent: bool


def run_weekly(
    settings: Settings | None = None,
    progress: Callable[[str], None] | None = None,
) -> WeeklyResult:
    """Generate this week's draft plan and email the review digest."""
    from app.notify.email import send_digest
    from app.plans import generate_and_save, get_plan

    settings = settings or get_settings()
    log = progress or (lambda _msg: None)

    log("Building this week's draft cart…")
    plan_id = generate_and_save(
        settings,
        horizon_days=settings.schedule_horizon_days,
        max_items=settings.schedule_max_items,
        budget=settings.weekly_budget,
        progress=log,
    )

    plan = get_plan(plan_id, settings)
    log("Sending the review digest…")
    digest = send_digest(plan, settings)
    log(digest.detail)
    return WeeklyResult(plan_id=plan_id, digest_detail=digest.detail, digest_sent=digest.sent)


def start_scheduler(
    settings: Settings | None = None,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Run a blocking weekly scheduler that fires :func:`run_weekly`.

    Requires the optional ``apscheduler`` dependency (``uv sync --extra scheduler``).
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "The scheduler needs the optional dependency. Install it with "
            "`uv sync --extra scheduler` (or `pip install apscheduler`)."
        ) from exc

    settings = settings or get_settings()
    log = progress or (lambda _msg: None)

    sched = BlockingScheduler()
    trigger = CronTrigger(day_of_week=settings.schedule_day, hour=settings.schedule_hour, minute=0)

    def job() -> None:
        try:
            run_weekly(settings, progress=log)
        except Exception as exc:  # keep the scheduler alive across a bad week
            log(f"Weekly run failed: {type(exc).__name__}: {exc}")

    sched.add_job(job, trigger, name="weekly-draft")
    log(
        f"Scheduler started — weekly draft every {settings.schedule_day} "
        f"at {settings.schedule_hour:02d}:00. Ctrl-C to stop."
    )
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover
        log("Scheduler stopped.")
