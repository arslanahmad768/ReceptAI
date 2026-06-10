"""
The actual function that APScheduler calls when a cron fires.
"""
import asyncio
import uuid
from app.database import get_db
from app.niches.dental import DentalConfig
from app.scraper.pipeline import run_pipeline
from app.cron.repository import update_last_run

NICHE_MAP = {"dental": DentalConfig}


def fire_scrape_job(cron_job_id: str) -> None:
    """Called by APScheduler on schedule."""
    db = get_db()
    cron_row = db.table("cron_jobs").select("*, niches(slug)").eq("id", cron_job_id).execute()
    if not cron_row.data:
        return
    cron = cron_row.data[0]
    niche_slug = cron.get("niches", {}).get("slug", "dental")

    if niche_slug not in NICHE_MAP:
        return

    job_id = str(uuid.uuid4())
    db.table("scrape_jobs").insert({
        "id": job_id, "niche_id": cron["niche_id"],
        "search_term": niche_slug, "location": cron["location"],
        "limit_count": cron["limit_count"],
        "status": "pending", "triggered_by": "cron",
    }).execute()

    niche_cfg = NICHE_MAP[niche_slug](
        niche_id=cron["niche_id"],
        location=cron["location"],
        limit=cron["limit_count"],
    )

    try:
        asyncio.run(run_pipeline(job_id, niche_cfg))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Pipeline failed for cron job %s: %s", cron_job_id, exc)
        db.table("scrape_jobs").update({"status": "failed"}).eq("id", job_id).execute()
        raise
    finally:
        update_last_run(cron_job_id)
