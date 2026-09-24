import uuid
from app.database import get_db

def create_cron_job(name: str, niche_id: str, location: str,
                    limit_count: int, cron_expr: str, user_id: str) -> dict:
    db = get_db()
    row = {
        "id": str(uuid.uuid4()), "name": name, "niche_id": niche_id,
        "location": location, "limit_count": limit_count,
        "cron_expr": cron_expr, "is_active": True, "created_by": user_id,
    }
    db.table("cron_jobs").insert(row).execute()
    return row

def list_cron_jobs(user_id: str) -> list[dict]:
    db = get_db()
    return db.table("cron_jobs").select("*").eq("created_by", user_id).execute().data or []

def get_cron_job(job_id: str) -> dict | None:
    db = get_db()
    r = db.table("cron_jobs").select("*").eq("id", job_id).execute()
    return r.data[0] if r.data else None

def delete_cron_job(job_id: str) -> bool:
    db = get_db()
    r = db.table("cron_jobs").delete().eq("id", job_id).execute()
    return bool(r.data)

def toggle_cron_job(job_id: str, is_active: bool) -> dict | None:
    db = get_db()
    r = db.table("cron_jobs").update({"is_active": is_active}).eq("id", job_id).execute()
    return r.data[0] if r.data else None

def update_last_run(job_id: str) -> None:
    db = get_db()
    db.table("cron_jobs").update({"last_run_at": "now()"}).eq("id", job_id).execute()

def list_active_cron_jobs() -> list[dict]:
    db = get_db()
    return db.table("cron_jobs").select("*, niches(slug)").eq("is_active", True).execute().data or []
