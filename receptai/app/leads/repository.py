from app.database import get_db

def list_leads(niche_slug: str | None = None, status: str | None = None) -> list[dict]:
    db = get_db()
    q = db.table("leads").select("*, clinics(name, website, niche_id, niches(slug))")
    if status:
        q = q.eq("status", status)
    rows = q.execute().data or []
    if niche_slug:
        rows = [r for r in rows
                if (r.get("clinics") or {}).get("niches", {}).get("slug") == niche_slug]
    return rows

def get_lead(lead_id: str) -> dict | None:
    db = get_db()
    r = db.table("leads").select("*, clinics(name, website)").eq("id", lead_id).execute()
    return r.data[0] if r.data else None

def update_lead_status(lead_id: str, status: str) -> dict | None:
    db = get_db()
    r = db.table("leads").update({"status": status, "updated_at": "now()"}).eq("id", lead_id).execute()
    return r.data[0] if r.data else None
