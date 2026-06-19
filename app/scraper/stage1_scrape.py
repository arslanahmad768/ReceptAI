"""Stage 1 — Camoufox scraper. Called by the pipeline; no file I/O."""
from datetime import datetime, timedelta, timezone
from app.scraper import maps_dom
from camoufox.sync_api import Camoufox


def run_stage1(
    search_term: str,
    location: str,
    limit: int = 10,
    cutoff_months: int = 12,
    headless: bool = True,
) -> list[dict]:
    """
    Returns list of clinic dicts:
      name, address, phone, website, google_maps_url,
      rating, review_count, reviews, sorted_newest
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * cutoff_months)
    full_term = f"{search_term} in {location}"
    clinics = []

    with Camoufox(headless=headless, humanize=True) as browser:
        page = browser.new_page()
        found = maps_dom.discover_clinics(page, full_term, limit)

        for f in found:
            try:
                c = maps_dom.scrape_place_reviews(page, f["google_maps_url"], cutoff)
                c["google_maps_url"] = f["google_maps_url"]
                c.setdefault("name", f["name"])
                clinics.append(c)
            except Exception as e:
                clinics.append({
                    "name": f["name"],
                    "google_maps_url": f["google_maps_url"],
                    "error": str(e),
                    "reviews": [],
                })
            maps_dom.pause(3, 6)

    return clinics


def run_stage1_at_coords(
    search_term: str,
    lat: float,
    lng: float,
    radius_km: float,
    limit: int = 20,
    cutoff_months: int = 12,
    headless: bool = True,
) -> tuple[list[dict], int]:
    """
    Coordinate-based Stage 1. Returns (clinics, raw_result_count).
    raw_result_count is the deduped count BEFORE the limit slice —
    used by the campaign service to detect cell saturation.
    """
    from app.scraper.grid_generator import radius_to_zoom
    zoom = radius_to_zoom(radius_km)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * cutoff_months)
    clinics: list[dict] = []
    with Camoufox(headless=headless, humanize=True) as browser:
        page = browser.new_page()
        found, raw_count = maps_dom.discover_clinics_at_coords(
            page, search_term, lat, lng, zoom, limit
        )
        for f in found:
            try:
                c = maps_dom.scrape_place_reviews(page, f["google_maps_url"], cutoff)
                c["google_maps_url"] = f["google_maps_url"]
                c.setdefault("name", f["name"])
                clinics.append(c)
            except Exception as e:
                clinics.append({
                    "name": f["name"],
                    "google_maps_url": f["google_maps_url"],
                    "error": str(e),
                    "reviews": [],
                })
            maps_dom.pause(3, 6)
    return clinics, raw_count
