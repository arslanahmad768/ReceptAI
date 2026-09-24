from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from app.auth.dependencies import get_current_user
from app.leads.models import LeadStatusUpdate
from app.leads.repository import list_leads, get_lead, update_lead_status

router = APIRouter(prefix="/leads", tags=["leads"])
VALID_STATUSES = {"new", "contacted", "replied", "converted", "rejected"}

@router.get("/")
async def get_leads(
    niche: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    return list_leads(niche_slug=niche, status=status)

@router.get("/{lead_id}")
async def get_lead_by_id(lead_id: str, user: dict = Depends(get_current_user)):
    lead = get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead

@router.patch("/{lead_id}")
async def patch_lead(
    lead_id: str,
    body: LeadStatusUpdate,
    user: dict = Depends(get_current_user),
):
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {VALID_STATUSES}")
    lead = update_lead_status(lead_id, body.status)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead
