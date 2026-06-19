import asyncio
import logging
import uuid

from app.database import get_db
from app.scraper.nominatim import fetch_bounding_box
from app.scraper.grid_generator import generate_initial_cells, subdivide_cell, GridCell
from app.campaigns import repository as repo

log = logging.getLogger("receptai.campaigns")
SATURATION_THRESHOLD = 20


async def create_campaign(
    niche_slug: str, city: str, country: str,
    initial_radius_km: float, max_depth: int,
    clinics_per_cell: int, created_by: str,
) -> dict:
    db = get_db()
    niche = db.table("niches").select("id").eq("slug", niche_slug).execute()
    if not niche.data:
        raise ValueError(f"Unknown niche: {niche_slug!r}")
    niche_id = niche.data[0]["id"]

    bbox = await asyncio.to_thread(fetch_bounding_box, city, country)
    location_id = repo.create_location(country, city, bbox)
    campaign = repo.create_campaign_record(
        niche_id, location_id, initial_radius_km, max_depth, clinics_per_cell, created_by
    )
    campaign_id = campaign["id"]

    initial_cells = generate_initial_cells(bbox, initial_radius_km)
    log.info("[campaign=%s] %s, %s → %d initial cells (r=%.1fkm)",
             campaign_id[:8], city, country, len(initial_cells), initial_radius_km)
    repo.store_cells(location_id, campaign_id, initial_cells)

    asyncio.create_task(_advance_campaign(campaign_id))
    return campaign


async def on_cell_complete(campaign_cell_id: str, raw_result_count: int) -> None:
    """Called by pipeline after Stage 1 completes for a campaign cell."""
    db = get_db()
    cc_rows = db.table("campaign_cells").select("*").eq("id", campaign_cell_id).execute()
    if not cc_rows.data:
        return
    cc = cc_rows.data[0]
    campaign_id = cc["campaign_id"]
    cell_id = cc["cell_id"]

    camp = db.table("scrape_campaigns").select("max_depth,location_id") \
        .eq("id", campaign_id).execute().data[0]
    cell = db.table("grid_cells").select("*").eq("id", cell_id).execute().data[0]

    depth = cell["depth"] if isinstance(cell, dict) else 0
    max_depth = camp["max_depth"] if isinstance(camp, dict) else SATURATION_THRESHOLD + 1
    if raw_result_count >= SATURATION_THRESHOLD and depth < max_depth:
        parent = GridCell(cell["center_lat"], cell["center_lng"], cell["radius_km"], cell["depth"])
        sub_cells = subdivide_cell(parent, parent_id=cell_id)
        repo.store_cells(camp["location_id"], campaign_id, sub_cells)
        repo.update_campaign_cell(campaign_cell_id, status="saturated", result_count=raw_result_count)
        log.info("[campaign=%s] Cell saturated (%d results, depth=%d) → +%d sub-cells",
                 campaign_id[:8], raw_result_count, cell["depth"], len(sub_cells))
    else:
        repo.update_campaign_cell(campaign_cell_id, status="done", result_count=raw_result_count)
        log.info("[campaign=%s] Cell done (%d results)", campaign_id[:8], raw_result_count)

    await _advance_campaign(campaign_id)


async def pause_campaign(campaign_id: str) -> None:
    repo.update_campaign(campaign_id, status="paused")
    log.info("[campaign=%s] Paused", campaign_id[:8])


async def resume_campaign(campaign_id: str) -> None:
    repo.update_campaign(campaign_id, status="running")
    asyncio.create_task(_advance_campaign(campaign_id))
    log.info("[campaign=%s] Resumed", campaign_id[:8])


async def _advance_campaign(campaign_id: str) -> None:
    db = get_db()
    camp_rows = db.table("scrape_campaigns") \
        .select("status,niche_id,initial_radius_km,clinics_per_cell,max_depth,location_id,created_by") \
        .eq("id", campaign_id).execute()
    if not camp_rows.data:
        return
    camp = camp_rows.data[0]
    if camp["status"] not in ("pending", "running"):
        return

    next_cell = repo.get_next_pending_cell(campaign_id)
    if not next_cell:
        if repo.count_active_cells(campaign_id) == 0:
            repo.update_campaign(campaign_id, status="complete")
            log.info("[campaign=%s] All cells processed — complete", campaign_id[:8])
        return

    campaign_cell_id = next_cell["id"]
    cell_id = next_cell["cell_id"]
    cell = db.table("grid_cells").select("*").eq("id", cell_id).execute().data[0]
    niche_slug = db.table("niches").select("slug").eq("id", camp["niche_id"]).execute().data[0]["slug"]

    job_id = str(uuid.uuid4())
    db.table("scrape_jobs").insert({
        "id": job_id, "niche_id": camp["niche_id"],
        "search_term": niche_slug,
        "location": f"{cell['center_lat']:.6f},{cell['center_lng']:.6f}",
        "limit_count": camp["clinics_per_cell"],
        "status": "pending", "triggered_by": "campaign",
        "created_by": camp.get("created_by"),
    }).execute()

    repo.update_campaign_cell(campaign_cell_id, status="running", scrape_job_id=job_id)
    repo.update_campaign(campaign_id, status="running")

    from app.niches.dental import DentalConfig
    NICHE_MAP = {"dental": DentalConfig}
    niche_cfg = NICHE_MAP[niche_slug](
        niche_id=camp["niche_id"],
        location=f"{cell['center_lat']:.6f},{cell['center_lng']:.6f}",
        limit=camp["clinics_per_cell"],
    )

    from app.scraper.pipeline import run_pipeline_at_coords
    asyncio.create_task(run_pipeline_at_coords(
        job_id, niche_cfg,
        lat=cell["center_lat"], lng=cell["center_lng"],
        radius_km=cell["radius_km"],
        campaign_cell_id=campaign_cell_id,
    ))
    log.info("[campaign=%s] → cell %s lat=%.4f lng=%.4f r=%.2fkm depth=%d",
             campaign_id[:8], campaign_cell_id[:8],
             cell["center_lat"], cell["center_lng"],
             cell["radius_km"], cell["depth"])
