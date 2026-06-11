import base64
import hmac
import hashlib
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from typing import Optional
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.leads.repository import get_lead
from app.niches.dental import DentalConfig
from app.outreach.email_generator import generate_cold_email
from app.outreach.campaign_service import (
    build_lead_shape,
    generate_drafts_for_campaign,
    send_campaign_emails,
)
from app.outreach.models import (
    GenerateEmailRequest,
    CampaignCreate,
    BulkGenerateRequest,
)
from app.outreach.repository import (
    create_campaign,
    list_campaigns,
    list_campaign_emails,
    get_email_by_resend_id,
    get_campaign,
)
from app.core.rate_limit import limiter
from app.config import settings

router = APIRouter(prefix="/outreach", tags=["outreach"])
NICHE_MAP = {"dental": DentalConfig}


def _resolve_niche(niche_slug: str, location: str = "") -> tuple:
    if niche_slug not in NICHE_MAP:
        raise HTTPException(400, f"Unknown niche '{niche_slug}'")
    db = get_db()
    row = db.table("niches").select("id").eq("slug", niche_slug).execute()
    if not row.data:
        raise HTTPException(400, "Niche not found in database")
    niche_id = row.data[0]["id"]
    return niche_id, NICHE_MAP[niche_slug](niche_id=niche_id, location=location)


@router.post("/generate")
@limiter.limit("30/minute")
async def generate_email_preview(
    request: Request,
    body: GenerateEmailRequest,
    user: dict = Depends(get_current_user),
):
    """Generate and preview a cold email for a single lead (does NOT send)."""
    _, niche_cfg = _resolve_niche(body.niche)
    lead_row = get_lead(body.lead_id)
    if not lead_row:
        raise HTTPException(404, "Lead not found")
    if not lead_row.get("email"):
        raise HTTPException(422, "Lead has no email — cannot generate outreach")
    if not lead_row.get("clinic_id"):
        raise HTTPException(status_code=422, detail="Lead has no clinic — cannot generate outreach")
    result = generate_cold_email(build_lead_shape(lead_row), niche_cfg)
    return {"lead_id": body.lead_id, "to_email": lead_row["email"], **result}


@router.post("/campaigns", status_code=201)
async def create_new_campaign(
    body: CampaignCreate,
    user: dict = Depends(get_current_user),
):
    niche_id, _ = _resolve_niche(body.niche)
    return create_campaign(body.name, niche_id, user["sub"])


@router.get("/campaigns")
async def get_campaigns(user: dict = Depends(get_current_user)):
    return list_campaigns(user["sub"])


@router.get("/campaigns/{campaign_id}/emails")
async def get_campaign_emails(campaign_id: str, user: dict = Depends(get_current_user)):
    campaign = get_campaign(campaign_id)
    if not campaign or campaign.get("created_by") != user["sub"]:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return list_campaign_emails(campaign_id)


@router.post("/campaigns/{campaign_id}/generate")
@limiter.limit("5/minute")
async def bulk_generate_drafts(
    request: Request,
    campaign_id: str,
    body: BulkGenerateRequest,
    user: dict = Depends(get_current_user),
):
    """Generate personalised draft emails for multiple leads at once."""
    campaign = get_campaign(campaign_id)
    if not campaign or campaign.get("created_by") != user["sub"]:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _, niche_cfg = _resolve_niche(body.niche)
    results = generate_drafts_for_campaign(campaign_id, body.lead_ids, niche_cfg)
    return {"campaign_id": campaign_id, "results": results}


@router.post("/campaigns/{campaign_id}/send")
@limiter.limit("2/minute")
async def send_campaign(
    request: Request,
    campaign_id: str,
    user: dict = Depends(get_current_user),
):
    """Send all draft emails in a campaign via Resend."""
    campaign = get_campaign(campaign_id)
    if not campaign or campaign.get("created_by") != user["sub"]:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return send_campaign_emails(campaign_id)


@router.post("/webhook/resend", include_in_schema=False)
async def resend_webhook(
    request: Request,
    svix_id: Optional[str] = Header(None),
    svix_signature: Optional[str] = Header(None),
    svix_timestamp: Optional[str] = Header(None),
):
    """
    Receives Resend delivery events (email.opened, email.bounced, email.complained).
    """
    body_bytes = await request.body()

    webhook_secret = getattr(settings, "resend_webhook_secret", "")
    if not webhook_secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    if not svix_signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    # always verify when we reach here
    to_sign = f"{svix_id}.{svix_timestamp}.{body_bytes.decode()}"
    sig_b64 = svix_signature.split(",")[-1].split("=", 1)[-1]
    expected_bytes = hmac.new(webhook_secret.encode(), to_sign.encode(), hashlib.sha256).digest()
    try:
        received_bytes = base64.b64decode(sig_b64)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed signature")
    if not hmac.compare_digest(expected_bytes, received_bytes):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = json.loads(body_bytes)
    event_type = event.get("type", "")
    message_id = (
        event.get("data", {}).get("email_id")
        or event.get("data", {}).get("message_id", "")
    )

    if not message_id:
        return {"received": True}

    db = get_db()
    if event_type == "email.opened":
        db.table("outreach_emails").update({"opened_at": datetime.now(timezone.utc).isoformat()}).eq(
            "resend_message_id", message_id
        ).execute()
    elif event_type in ("email.bounced", "email.complained"):
        db.table("outreach_emails").update({"status": "bounced"}).eq(
            "resend_message_id", message_id
        ).execute()

    return {"received": True}
