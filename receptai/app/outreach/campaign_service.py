"""
Orchestrates: generate emails for a list of leads → save as draft → send campaign.
"""
from app.database import get_db
from app.leads.repository import get_lead
from app.outreach.email_generator import generate_cold_email
from app.outreach.sender import send_email
from app.outreach.repository import add_draft_email, mark_email_sent, mark_email_failed
from app.niches.base import NicheConfig


def build_lead_shape(lead_row: dict) -> dict:
    """Reshape a leads+qualifications DB row into the format expected by generate_cold_email."""
    db = get_db()
    clinic_row = lead_row.get("clinics") or {}
    qual_rows = db.table("qualifications").select("*").eq(
        "clinic_id", lead_row["clinic_id"]
    ).execute()
    qual = qual_rows.data[0] if qual_rows.data else {}
    return {
        "clinic": {
            "name": clinic_row.get("name"),
            "website": clinic_row.get("website"),
        },
        "qualification": {
            "personalization_hook": qual.get("personalization_hook", ""),
            "complaint_quote": qual.get("complaint_quote"),
            "primary_pain_point": qual.get("primary_pain_point", ""),
        },
    }


def generate_drafts_for_campaign(
    campaign_id: str, lead_ids: list[str], niche_cfg: NicheConfig
) -> list[dict]:
    """Generate cold emails for each lead and save them as drafts."""
    results = []
    for lead_id in lead_ids:
        lead_row = get_lead(lead_id)
        if not lead_row or not lead_row.get("email"):
            results.append({"lead_id": lead_id, "status": "skipped_no_email"})
            continue
        lead_shape = build_lead_shape(lead_row)
        email = generate_cold_email(lead_shape, niche_cfg)
        draft = add_draft_email(
            campaign_id,
            lead_id,
            lead_row["email"],
            email["subject"],
            email["body"],
        )
        results.append(
            {"lead_id": lead_id, "draft_id": draft["id"], "status": "draft_created"}
        )
    return results


def send_campaign_emails(campaign_id: str) -> dict:
    """Send all draft emails in a campaign via Resend."""
    db = get_db()
    drafts = (
        db.table("outreach_emails")
        .select("*")
        .eq("campaign_id", campaign_id)
        .eq("status", "draft")
        .execute()
        .data
        or []
    )

    sent, failed = 0, 0
    for draft in drafts:
        result = send_email(draft["to_email"], draft["subject"], draft["body"])
        if result.success:
            mark_email_sent(draft["id"], result.message_id)
            db.table("leads").update({"status": "contacted"}).eq(
                "id", draft["lead_id"]
            ).execute()
            sent += 1
        else:
            mark_email_failed(draft["id"], result.error or "unknown")
            failed += 1

    if drafts:
        db.table("campaigns").update(
            {"status": "active" if sent > 0 else "draft"}
        ).eq("id", campaign_id).execute()

    return {"sent": sent, "failed": failed, "total": len(drafts)}
