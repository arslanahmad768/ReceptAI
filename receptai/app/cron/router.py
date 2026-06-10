from fastapi import APIRouter, Depends, HTTPException, Response
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.cron.models import CronJobCreate
from app.cron.repository import (
    create_cron_job, list_cron_jobs, get_cron_job,
    delete_cron_job, toggle_cron_job
)
from app.cron.scheduler import register_cron_job, unregister_cron_job
from app.cron.jobs import NICHE_MAP

router = APIRouter(prefix="/cron", tags=["cron"])
VALID_NICHES = set(NICHE_MAP.keys())


@router.post("/jobs", status_code=201)
async def create_job(body: CronJobCreate, user: dict = Depends(get_current_user)):
    if body.niche not in VALID_NICHES:
        raise HTTPException(400, f"Unknown niche '{body.niche}'")
    parts = body.cron_expr.strip().split()
    if len(parts) != 5:
        raise HTTPException(400, "cron_expr must be a 5-part cron string e.g. '0 9 * * 1'")

    db = get_db()
    niche_row = db.table("niches").select("id").eq("slug", body.niche).execute()
    if not niche_row.data:
        raise HTTPException(400, "Niche not in database")

    job = create_cron_job(
        name=body.name,
        niche_id=niche_row.data[0]["id"],
        location=body.location,
        limit_count=body.limit_count,
        cron_expr=body.cron_expr,
        user_id=user["sub"],
    )
    try:
        register_cron_job(job["id"], body.cron_expr)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return job


@router.get("/jobs")
async def list_jobs(user: dict = Depends(get_current_user)):
    return list_cron_jobs(user["sub"])


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(get_current_user)):
    job = get_cron_job(job_id)
    if not job:
        raise HTTPException(404, "Cron job not found")
    if job["created_by"] != user["sub"]:
        raise HTTPException(403, "Forbidden")
    return job


@router.patch("/jobs/{job_id}/toggle")
async def toggle_job(job_id: str, user: dict = Depends(get_current_user)):
    job = get_cron_job(job_id)
    if not job:
        raise HTTPException(404, "Cron job not found")
    if job["created_by"] != user["sub"]:
        raise HTTPException(403, "Forbidden")
    updated = toggle_cron_job(job_id, not job["is_active"])
    if updated["is_active"]:
        register_cron_job(job_id, job["cron_expr"])
    else:
        unregister_cron_job(job_id)
    return updated


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, user: dict = Depends(get_current_user)):
    job = get_cron_job(job_id)
    if not job:
        raise HTTPException(404, "Cron job not found")
    if job["created_by"] != user["sub"]:
        raise HTTPException(403, "Forbidden")
    delete_cron_job(job_id)
    unregister_cron_job(job_id)
    return Response(status_code=204)
