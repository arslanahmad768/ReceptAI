from app.database import get_db
from app.scraper.grid_generator import GridCell


def create_location(country: str, city: str, bbox: dict) -> str:
    db = get_db()
    result = db.table("locations").upsert(
        {"country": country, "city": city, **bbox},
        on_conflict="country,city",
    ).execute()
    return result.data[0]["id"]


def create_campaign_record(
    niche_id: str, location_id: str, radius_km: float,
    max_depth: int, clinics_per_cell: int, created_by: str,
) -> dict:
    db = get_db()
    return db.table("scrape_campaigns").insert({
        "niche_id": niche_id, "location_id": location_id,
        "initial_radius_km": radius_km, "max_depth": max_depth,
        "clinics_per_cell": clinics_per_cell, "created_by": created_by,
        "status": "pending",
    }).execute().data[0]


def store_cells(location_id: str, campaign_id: str, cells: list[GridCell]) -> None:
    db = get_db()
    for cell in cells:
        cell_row = db.table("grid_cells").insert({
            "location_id": location_id,
            "center_lat": cell.center_lat,
            "center_lng": cell.center_lng,
            "radius_km": cell.radius_km,
            "depth": cell.depth,
            "parent_id": cell.parent_id,
        }).execute()
        cell_id = cell_row.data[0]["id"]
        db.table("campaign_cells").insert({
            "campaign_id": campaign_id,
            "cell_id": cell_id,
            "status": "pending",
        }).execute()


def get_next_pending_cell(campaign_id: str) -> dict | None:
    db = get_db()
    rows = (
        db.table("campaign_cells")
        .select("id,cell_id")
        .eq("campaign_id", campaign_id)
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    return rows.data[0] if rows.data else None


def update_campaign_cell(campaign_cell_id: str, **kwargs) -> None:
    get_db().table("campaign_cells").update(kwargs).eq("id", campaign_cell_id).execute()


def update_campaign(campaign_id: str, **kwargs) -> None:
    get_db().table("scrape_campaigns").update(kwargs).eq("id", campaign_id).execute()


def count_active_cells(campaign_id: str) -> int:
    db = get_db()
    rows = (
        db.table("campaign_cells")
        .select("id")
        .eq("campaign_id", campaign_id)
        .in_("status", ["pending", "running"])
        .execute()
    )
    return len(rows.data)


def get_campaign_with_stats(campaign_id: str) -> dict:
    db = get_db()
    camp = (
        db.table("scrape_campaigns")
        .select("*, locations(city, country), niches(slug)")
        .eq("id", campaign_id)
        .execute()
    )
    if not camp.data:
        return {}
    c = camp.data[0]
    cells = (
        db.table("campaign_cells")
        .select("status")
        .eq("campaign_id", campaign_id)
        .execute()
        .data
    )
    statuses = [row["status"] for row in cells]
    job_ids = [
        row["scrape_job_id"]
        for row in db.table("campaign_cells")
        .select("scrape_job_id")
        .eq("campaign_id", campaign_id)
        .not_.is_("scrape_job_id", "null")
        .execute()
        .data
    ]
    clinics_count = 0
    if job_ids:
        res = db.table("clinics").select("id", count="exact").in_("job_id", job_ids).execute()
        clinics_count = res.count or 0
    return {
        **c,
        "city": c["locations"]["city"],
        "country": c["locations"]["country"],
        "total_cells": len(cells),
        "done_cells": statuses.count("done"),
        "running_cells": statuses.count("running"),
        "pending_cells": statuses.count("pending"),
        "saturated_cells": statuses.count("saturated"),
        "clinics_found": clinics_count,
    }


def list_campaigns(created_by: str) -> list[dict]:
    db = get_db()
    return (
        db.table("scrape_campaigns")
        .select("*, locations(city, country)")
        .eq("created_by", created_by)
        .order("created_at", desc=True)
        .execute()
        .data
    )
