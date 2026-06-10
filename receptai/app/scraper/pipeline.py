"""Orchestrates Stage 1 → 2 → 3, saving each stage to Supabase."""
import asyncio
import uuid
from datetime import datetime, timezone
from app.database import get_db
from app.scraper.stage1_scrape import run_stage1
from app.scraper.stage2_categorize import categorize_clinic
from app.scraper.stage3_emails import find_email_for_clinic
from app.niches.base import NicheConfig


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_pipeline(job_id: str, niche_config: NicheConfig) -> None:
    db = get_db()
    db.table("scrape_jobs").update({"status": "running"}).eq("id", job_id).execute()
    try:
        raw_clinics = run_stage1(
            search_term=niche_config.search_term,
            location=niche_config.location,
            limit=niche_config.limit,
            cutoff_months=niche_config.cutoff_months,
        )
        saved_rows = []
        for c in raw_clinics:
            row_id = str(uuid.uuid4())
            row = {
                "id": row_id, "job_id": job_id, "niche_id": niche_config.niche_id,
                "name": c.get("name"), "address": c.get("address"),
                "phone": c.get("phone"), "website": c.get("website"),
                "google_maps_url": c.get("google_maps_url"),
                "rating": c.get("rating"), "review_count": c.get("review_count"),
                "reviews_json": c.get("reviews", []),
            }
            result = db.table("clinics").upsert(row, on_conflict="google_maps_url").execute()
            saved_id = result.data[0]["id"] if result.data else row_id
            saved_rows.append((saved_id, c))

        for clinic_id, raw in saved_rows:
            q = categorize_clinic(raw, niche_config.categorize_system_prompt)
            db.table("qualifications").upsert({
                "clinic_id": clinic_id,
                "qualification_score": q.get("qualification_score"),
                "primary_pain_point": q.get("primary_pain_point"),
                "complaint_quote": q.get("complaint_quote"),
                "personalization_hook": q.get("personalization_hook"),
                "recommend_outreach": q.get("recommend_outreach", False),
                "model_used": "llama-3.3-70b-versatile",
            }, on_conflict="clinic_id").execute()

        for clinic_id, raw in saved_rows:
            website = raw.get("website")
            if not website:
                continue
            result = await find_email_for_clinic(website, raw.get("name", ""))
            db.table("leads").upsert({
                "clinic_id": clinic_id,
                "email": result["email"],
                "email_method": result["method"],
                "all_emails": result["all_emails"],
                "doctors_found": result["doctors_found"],
                "status": "new",
            }, on_conflict="clinic_id").execute()

        db.table("scrape_jobs").update({
            "status": "done", "finished_at": _now()
        }).eq("id", job_id).execute()

    except Exception as e:
        db.table("scrape_jobs").update({
            "status": "failed", "error_msg": str(e)[:500], "finished_at": _now()
        }).eq("id", job_id).execute()
        raise
