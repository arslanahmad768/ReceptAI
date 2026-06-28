"""Orchestrates Stage 1 → 2 → 3, saving each stage to Supabase."""
import asyncio
import logging
from datetime import datetime, timezone
from app.database import get_db
from app.scraper.stage1_scrape import run_stage1, run_stage1_at_coords
from app.scraper.stage2_categorize import categorize_clinic
from app.scraper.stage3_emails import find_email_for_clinic
from app.niches.base import NicheConfig
from app.campaigns.service import on_cell_complete

log = logging.getLogger("receptai.pipeline")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _progress(db, job_id: str, stage: int, message: str, **counts) -> None:
    log.info("[job=%s] stage=%d  %s  %s", job_id[:8], stage, message,
             "  ".join(f"{k}={v}" for k, v in counts.items()) if counts else "")
    db.table("scrape_jobs").update({
        "progress": {"stage": stage, "message": message, **counts}
    }).eq("id", job_id).execute()


async def _process_clinics(
    job_id: str,
    niche_config: NicheConfig,
    raw_clinics: list[dict],
) -> tuple[int, int, int]:
    """
    Shared Stage 2 + Stage 3 logic used by both pipeline variants.
    Deduplicates against existing DB records, qualifies, finds emails.
    Returns (new_total, qualified, emails_found).
    """
    db = get_db()
    total = len(raw_clinics)

    # ── Deduplicate against existing DB records ────────────────────────────
    scraped_urls = [c["google_maps_url"] for c in raw_clinics if c.get("google_maps_url")]
    existing_rows = (
        db.table("clinics").select("google_maps_url")
        .in_("google_maps_url", scraped_urls)
        .execute()
    )
    existing_urls = {r["google_maps_url"] for r in existing_rows.data if r.get("google_maps_url")}
    new_clinics = [c for c in raw_clinics if c.get("google_maps_url") not in existing_urls]
    already_known = total - len(new_clinics)

    log.info("[job=%s] %d new  |  %d already in DB — skipping",
             job_id[:8], len(new_clinics), already_known)
    _progress(db, job_id, 1,
              f"{len(new_clinics)} new clinics to process, {already_known} skipped (already in DB)",
              clinics_found=total, new=len(new_clinics), skipped_duplicate=already_known)

    # ── Insert new clinics only ────────────────────────────────────────────
    saved_rows = []
    for c in new_clinics:
        row = {
            "job_id": job_id, "niche_id": niche_config.niche_id,
            "name": c.get("name"), "address": c.get("address"),
            "phone": c.get("phone"), "website": c.get("website"),
            "google_maps_url": c.get("google_maps_url"),
            "rating": c.get("rating"), "review_count": c.get("review_count"),
            "reviews_json": c.get("reviews", []),
        }
        result = db.table("clinics").insert(row).execute()
        saved_id = result.data[0]["id"]
        saved_rows.append((saved_id, c))

    new_total = len(saved_rows)

    # ── Stage 2: LLM qualification ────────────────────────────────────────
    qualified = 0
    for i, (clinic_id, raw) in enumerate(saved_rows, 1):
        _progress(db, job_id, 2,
                  f"Qualifying clinic {i}/{new_total}: {raw.get('name', '...')}",
                  clinics_found=total, new=new_total, current=i, total=new_total,
                  qualified=qualified)
        q = await asyncio.to_thread(categorize_clinic, raw, niche_config.categorize_system_prompt)
        if q.get("recommend_outreach"):
            qualified += 1
        db.table("qualifications").upsert({
            "clinic_id": clinic_id,
            "qualification_score": q.get("qualification_score"),
            "primary_pain_point": q.get("primary_pain_point"),
            "complaint_quote": q.get("complaint_quote"),
            "personalization_hook": q.get("personalization_hook"),
            "recommend_outreach": q.get("recommend_outreach", False),
            "model_used": "llama-3.3-70b-versatile",
        }, on_conflict="clinic_id").execute()
    _progress(db, job_id, 2, f"Qualified {qualified}/{new_total} new clinics",
              clinics_found=total, new=new_total, qualified=qualified)

    # ── Stage 3: Email discovery ───────────────────────────────────────────
    emails_found = 0
    skipped_no_site = 0
    for i, (clinic_id, raw) in enumerate(saved_rows, 1):
        website = raw.get("website")
        if not website:
            skipped_no_site += 1
            continue
        _progress(db, job_id, 3,
                  f"Finding email {i}/{new_total}: {raw.get('name', '...')}",
                  clinics_found=total, new=new_total, qualified=qualified,
                  emails_found=emails_found, current=i, total=new_total)
        result = await find_email_for_clinic(
            website,
            raw.get("name", ""),
        )
        if result.get("email"):
            emails_found += 1
        db.table("leads").upsert({
            "clinic_id": clinic_id,
            "email": result["email"],
            "email_method": result["method"],
            "contact_role": result.get("contact_role"),
            "all_emails": result["all_emails"],
            "doctors_found": result["doctors_found"],
            "status": "new",
        }, on_conflict="clinic_id").execute()

    return new_total, qualified, emails_found


async def run_pipeline(job_id: str, niche_config: NicheConfig) -> None:
    db = get_db()
    db.table("scrape_jobs").update({"status": "running"}).eq("id", job_id).execute()
    try:
        # ── Stage 1: Google Maps scrape ───────────────────────────────────────
        _progress(db, job_id, 1, "Scraping Google Maps...")
        raw_clinics = await asyncio.to_thread(
            run_stage1,
            search_term=niche_config.search_term,
            location=niche_config.location,
            limit=niche_config.limit,
            cutoff_months=niche_config.cutoff_months,
        )
        total = len(raw_clinics)
        _progress(db, job_id, 1, f"Found {total} clinics — checking for duplicates...",
                  clinics_found=total)

        new_total, qualified, emails_found = await _process_clinics(job_id, niche_config, raw_clinics)

        # Recompute already_known for the final status update
        already_known = total - new_total

        db.table("scrape_jobs").update({
            "status": "done",
            "finished_at": _now(),
            "progress": {
                "stage": 3,
                "message": "Complete",
                "clinics_found": total,
                "skipped_duplicate": already_known,
                "new": new_total,
                "qualified": qualified,
                "emails_found": emails_found,
            },
        }).eq("id", job_id).execute()

    except Exception as e:
        db.table("scrape_jobs").update({
            "status": "failed", "error_msg": str(e)[:500], "finished_at": _now()
        }).eq("id", job_id).execute()
        raise


async def run_pipeline_at_coords(
    job_id: str,
    niche_config: NicheConfig,
    lat: float,
    lng: float,
    radius_km: float,
    campaign_cell_id: str | None = None,
) -> None:
    """Coordinate-based pipeline variant used by the campaign system."""
    db = get_db()
    db.table("scrape_jobs").update({"status": "running"}).eq("id", job_id).execute()
    try:
        _progress(db, job_id, 1, f"Scraping ({lat:.4f}, {lng:.4f}) r={radius_km}km...")
        raw_clinics, raw_count = await asyncio.to_thread(
            run_stage1_at_coords,
            search_term=niche_config.search_term,
            lat=lat,
            lng=lng,
            radius_km=radius_km,
            limit=niche_config.limit,
            cutoff_months=niche_config.cutoff_months,
        )
        _progress(db, job_id, 1, f"Found {len(raw_clinics)} clinics (raw={raw_count})",
                  clinics_found=len(raw_clinics), raw_count=raw_count)

        if campaign_cell_id:
            await on_cell_complete(campaign_cell_id, raw_count)

        new_total, qualified, emails_found = await _process_clinics(job_id, niche_config, raw_clinics)

        db.table("scrape_jobs").update({
            "status": "done",
            "finished_at": _now(),
            "progress": {
                "stage": 3,
                "message": "Complete",
                "raw_count": raw_count,
                "new": new_total,
                "qualified": qualified,
                "emails_found": emails_found,
            },
        }).eq("id", job_id).execute()

    except Exception as e:
        db.table("scrape_jobs").update({
            "status": "failed", "error_msg": str(e)[:500], "finished_at": _now()
        }).eq("id", job_id).execute()
        raise
