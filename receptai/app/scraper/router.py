import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.requests import Request
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.niches.dental import DentalConfig
from app.scraper.pipeline import run_pipeline
from app.core.rate_limit import limiter

router = APIRouter(prefix="/scraper", tags=["scraper"])
NICHE_MAP = {"dental": DentalConfig}


class JobRequest(BaseModel):
    niche: str = "dental"
    location: str
    limit: int = 10
    cutoff_months: int = 12


@router.post("/jobs", status_code=201)
@limiter.limit("3/minute")
async def create_job(
    request: Request,
    body: JobRequest,
    user: dict = Depends(get_current_user),
):
    if body.niche not in NICHE_MAP:
        raise HTTPException(400, f"Unknown niche. Available: {list(NICHE_MAP)}")
    db = get_db()
    niche_row = db.table("niches").select("id").eq("slug", body.niche).execute()
    if not niche_row.data:
        raise HTTPException(400, "Niche not found in database")

    job_id = str(uuid.uuid4())
    db.table("scrape_jobs").insert({
        "id": job_id, "niche_id": niche_row.data[0]["id"],
        "search_term": body.niche, "location": body.location,
        "limit_count": body.limit, "status": "pending",
        "triggered_by": "manual", "created_by": user["sub"],
    }).execute()

    niche_cfg = NICHE_MAP[body.niche](
        niche_id=niche_row.data[0]["id"],
        location=body.location,
        limit=body.limit,
        cutoff_months=body.cutoff_months,
    )
    asyncio.create_task(run_pipeline(job_id, niche_cfg))
    return {"job_id": job_id, "status": "pending"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    row = db.table("scrape_jobs").select("*").eq("id", job_id).execute()
    if not row.data:
        raise HTTPException(404, "Job not found")
    return row.data[0]


@router.get("/jobs")
async def list_jobs(user: dict = Depends(get_current_user)):
    db = get_db()
    return db.table("scrape_jobs").select("*").order("created_at", desc=True).limit(50).execute().data
