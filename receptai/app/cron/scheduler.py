"""
APScheduler singleton. Started in FastAPI lifespan.
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
    return _scheduler


def start_scheduler() -> None:
    from app.cron.repository import list_active_cron_jobs

    scheduler = get_scheduler()
    jobs = list_active_cron_jobs()
    for job in jobs:
        _register_job(scheduler, job["id"], job["cron_expr"])
    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _register_job(scheduler: BackgroundScheduler, cron_job_id: str, cron_expr: str) -> None:
    from app.cron.jobs import fire_scrape_job
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression '{cron_expr}' — must have 5 parts")
    minute, hour, day, month, day_of_week = parts
    trigger = CronTrigger(
        minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week,
        timezone="UTC"
    )
    scheduler.add_job(
        fire_scrape_job,
        trigger=trigger,
        args=[cron_job_id],
        id=cron_job_id,
        replace_existing=True,
    )


def register_cron_job(cron_job_id: str, cron_expr: str) -> None:
    """Call this after creating a new cron_job row so it's active immediately."""
    _register_job(get_scheduler(), cron_job_id, cron_expr)


def unregister_cron_job(cron_job_id: str) -> None:
    """Call this when a cron_job is deleted or paused."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(cron_job_id)
    except Exception:
        pass
