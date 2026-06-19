# app/campaigns/router.py
from fastapi import APIRouter, Depends, HTTPException
from app.auth.dependencies import get_current_user
from app.campaigns.models import CampaignCreate
from app.campaigns.service import create_campaign, pause_campaign, resume_campaign
from app.campaigns import repository as repo

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", status_code=201)
async def create(body: CampaignCreate, user: dict = Depends(get_current_user)):
    try:
        return await create_campaign(
            niche_slug=body.niche, city=body.city, country=body.country,
            initial_radius_km=body.initial_radius_km, max_depth=body.max_depth,
            clinics_per_cell=body.clinics_per_cell, created_by=user["sub"],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("")
def list_all(user: dict = Depends(get_current_user)):
    return repo.list_campaigns(created_by=user["sub"])


@router.get("/{campaign_id}")
def get_stats(campaign_id: str, user: dict = Depends(get_current_user)):
    stats = repo.get_campaign_with_stats(campaign_id)
    if not stats:
        raise HTTPException(404, "Campaign not found")
    if stats.get("created_by") != user["sub"]:
        raise HTTPException(403, "Forbidden")
    return stats


@router.post("/{campaign_id}/pause")
async def pause(campaign_id: str, user: dict = Depends(get_current_user)):
    stats = repo.get_campaign_with_stats(campaign_id)
    if not stats:
        raise HTTPException(404, "Campaign not found")
    if stats.get("created_by") != user["sub"]:
        raise HTTPException(403, "Forbidden")
    await pause_campaign(campaign_id)
    return {"status": "paused"}


@router.post("/{campaign_id}/resume")
async def resume(campaign_id: str, user: dict = Depends(get_current_user)):
    stats = repo.get_campaign_with_stats(campaign_id)
    if not stats:
        raise HTTPException(404, "Campaign not found")
    if stats.get("created_by") != user["sub"]:
        raise HTTPException(403, "Forbidden")
    await resume_campaign(campaign_id)
    return {"status": "running"}
