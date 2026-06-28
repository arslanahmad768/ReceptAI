import uuid
from app.database import get_db
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_campaign(name: str, niche_id: str, user_id: str) -> dict:
    db = get_db()
    row = {
        "id": str(uuid.uuid4()),
        "name": name,
        "niche_id": niche_id,
        "created_by": user_id,
        "status": "draft",
    }
    db.table("campaigns").insert(row).execute()
    return row


def list_campaigns(user_id: str) -> list[dict]:
    db = get_db()
    return db.table("campaigns").select("*").eq("created_by", user_id).execute().data or []


def add_draft_email(
    campaign_id: str, lead_id: str, to_email: str, subject: str, body: str
) -> dict:
    db = get_db()
    row = {
        "id": str(uuid.uuid4()),
        "campaign_id": campaign_id,
        "lead_id": lead_id,
        "to_email": to_email,
        "subject": subject,
        "body": body,
        "status": "draft",
    }
    db.table("outreach_emails").insert(row).execute()
    return row


def mark_email_sent(email_id: str, message_id: str) -> None:
    db = get_db()
    db.table("outreach_emails").update(
        {"status": "sent", "resend_message_id": message_id, "sent_at": _now()}
    ).eq("id", email_id).execute()


def mark_email_failed(email_id: str, error: str) -> None:
    db = get_db()
    db.table("outreach_emails").update(
        {"status": "failed", "send_error": error}
    ).eq("id", email_id).execute()


def get_campaign(campaign_id: str) -> dict | None:
    db = get_db()
    r = db.table("campaigns").select("*").eq("id", campaign_id).execute()
    return r.data[0] if r.data else None


def list_campaign_emails(campaign_id: str) -> list[dict]:
    db = get_db()
    return (
        db.table("outreach_emails")
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
        .data
        or []
    )


def get_email_by_resend_id(resend_message_id: str) -> dict | None:
    db = get_db()
    r = (
        db.table("outreach_emails")
        .select("*,leads(clinic_id)")
        .eq("resend_message_id", resend_message_id)
        .execute()
    )
    return r.data[0] if r.data else None
