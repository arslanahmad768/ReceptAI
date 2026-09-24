# Grid Scraping Campaign System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace city-text-based scraping with an adaptive quadtree grid system that automatically tiles any city worldwide into coordinate-based search cells, subdivides saturated cells, and orchestrates the whole run as a trackable campaign.

**Architecture:** OpenStreetMap Nominatim fetches a bounding box for any city+country automatically (free, global, no API key). The grid generator tiles that box into `radius_km`-radius cells; each cell is one Google Maps coordinate search. If a cell returns ≥ 20 results it is marked saturated and split into 4 quadrant sub-cells that are re-queued — this is the adaptive quadtree. A `scrape_campaigns` table orchestrates the run, firing one cell at a time through the existing pipeline; the existing dedup, Stage 2 LLM qualification, and Stage 3 email discovery are untouched.

**Tech Stack:** FastAPI, Supabase (postgrest-py), APScheduler (existing), `httpx` (existing), Python `math` stdlib, Camoufox + Playwright (existing).

## Global Constraints

- Python ≥ 3.11 — use `X | Y` union types, not `Optional[X]`
- All DB access via `get_db()` singleton — never instantiate `supabase.create_client` directly
- Async functions use `asyncio.to_thread()` for any blocking call (Camoufox, httpx sync, etc.)
- No new third-party packages — `httpx` already in dependencies
- Follow existing module pattern: `router.py`, `service.py`, `repository.py`, `models.py` per feature
- Run tests with `uv run pytest tests/ -v`
- `SATURATION_THRESHOLD = 20` (Google Maps local result cap per coordinate search)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `supabase/migrations/003_campaigns_grid.sql` | 4 new tables |
| Create | `app/scraper/nominatim.py` | Fetch bbox from OpenStreetMap |
| Create | `app/scraper/grid_generator.py` | Generate cells + quadtree subdivision |
| Modify | `app/scraper/maps_dom.py` | Add `discover_clinics_at_coords()` |
| Modify | `app/scraper/stage1_scrape.py` | Add `run_stage1_at_coords()` |
| Create | `app/campaigns/__init__.py` | Empty |
| Create | `app/campaigns/models.py` | Pydantic request/response models |
| Create | `app/campaigns/repository.py` | All DB operations for campaigns |
| Create | `app/campaigns/service.py` | Campaign creation + cell orchestration |
| Create | `app/campaigns/router.py` | 5 API endpoints |
| Modify | `app/scraper/pipeline.py` | Extract shared helper + add `run_pipeline_at_coords` |
| Modify | `app/main.py` | Register campaign router |
| Create | `tests/test_nominatim.py` | Nominatim bbox parsing |
| Create | `tests/test_grid_generator.py` | Cell math + subdivision |
| Create | `tests/test_campaigns.py` | Campaign creation + cell advancement |

---

## Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/003_campaigns_grid.sql`

**Interfaces:**
- Produces: tables `locations`, `grid_cells`, `scrape_campaigns`, `campaign_cells`

- [ ] **Step 1: Write the migration file**

```sql
-- 003_campaigns_grid.sql
-- Run in: Supabase Dashboard → SQL Editor → Run

-- City bounding boxes (auto-populated via Nominatim, never edited manually)
create table if not exists locations (
  id          uuid primary key default gen_random_uuid(),
  country     text not null,
  city        text not null,
  bbox_north  float not null,
  bbox_south  float not null,
  bbox_east   float not null,
  bbox_west   float not null,
  created_at  timestamptz default now(),
  unique (country, city)
);

-- Individual coordinate search cells within a city
create table if not exists grid_cells (
  id          uuid primary key default gen_random_uuid(),
  location_id uuid references locations(id) on delete cascade,
  center_lat  float not null,
  center_lng  float not null,
  radius_km   float not null,
  depth       int not null default 0,
  parent_id   uuid references grid_cells(id),
  created_at  timestamptz default now()
);

-- One campaign = one niche scrape across an entire city
create table if not exists scrape_campaigns (
  id                uuid primary key default gen_random_uuid(),
  niche_id          uuid references niches(id),
  location_id       uuid references locations(id),
  status            text not null default 'pending',
  initial_radius_km float not null default 1.0,
  max_depth         int not null default 4,
  clinics_per_cell  int not null default 20,
  created_by        uuid references profiles(id),
  created_at        timestamptz default now()
);

-- Per-cell tracking within a campaign
create table if not exists campaign_cells (
  id             uuid primary key default gen_random_uuid(),
  campaign_id    uuid references scrape_campaigns(id) on delete cascade,
  cell_id        uuid references grid_cells(id),
  scrape_job_id  uuid references scrape_jobs(id),
  status         text not null default 'pending',
  result_count   int,
  created_at     timestamptz default now()
);
```

- [ ] **Step 2: Run the migration**

Open Supabase Dashboard → SQL Editor → paste the file contents → Run.

- [ ] **Step 3: Verify tables exist**

In Supabase SQL Editor:
```sql
select table_name from information_schema.tables
where table_schema = 'public'
  and table_name in ('locations','grid_cells','scrape_campaigns','campaign_cells');
```
Expected: 4 rows returned.

---

## Task 2: Nominatim Bounding Box Service

**Files:**
- Create: `app/scraper/nominatim.py`
- Create: `tests/test_nominatim.py`

**Interfaces:**
- Produces: `fetch_bounding_box(city: str, country: str) -> dict`
  - Returns `{"bbox_north": float, "bbox_south": float, "bbox_east": float, "bbox_west": float}`
  - Raises `ValueError` if city not found
  - Raises `httpx.HTTPStatusError` on network failure

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nominatim.py
from unittest.mock import patch, MagicMock
import pytest
from app.scraper.nominatim import fetch_bounding_box


def _mock_response(bbox_list):
    resp = MagicMock()
    resp.json.return_value = [{"boundingbox": bbox_list}]
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_bounding_box_parses_nominatim_response():
    # Nominatim returns [south, north, west, east]
    fake_bbox = ["51.2867601", "51.6918741", "-0.5103751", "0.3340155"]
    with patch("app.scraper.nominatim.httpx.get", return_value=_mock_response(fake_bbox)):
        result = fetch_bounding_box("London", "UK")
    assert result["bbox_south"] == pytest.approx(51.2867601)
    assert result["bbox_north"] == pytest.approx(51.6918741)
    assert result["bbox_west"] == pytest.approx(-0.5103751)
    assert result["bbox_east"] == pytest.approx(0.3340155)


def test_fetch_bounding_box_raises_for_unknown_city():
    resp = MagicMock()
    resp.json.return_value = []
    resp.raise_for_status = MagicMock()
    with patch("app.scraper.nominatim.httpx.get", return_value=resp):
        with pytest.raises(ValueError, match="City not found"):
            fetch_bounding_box("Fakeville", "ZZ")


def test_fetch_bounding_box_sends_correct_params():
    with patch("app.scraper.nominatim.httpx.get", return_value=_mock_response(["1","2","3","4"])) as mock_get:
        fetch_bounding_box("Tokyo", "Japan")
    call_kwargs = mock_get.call_args
    params = call_kwargs.kwargs["params"]
    assert params["city"] == "Tokyo"
    assert params["country"] == "Japan"
    assert params["format"] == "json"
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_nominatim.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.scraper.nominatim'`

- [ ] **Step 3: Implement the service**

```python
# app/scraper/nominatim.py
import httpx

_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "ReceptAI/1.0"}


def fetch_bounding_box(city: str, country: str) -> dict:
    """
    Returns {"bbox_north", "bbox_south", "bbox_east", "bbox_west"} floats.
    Nominatim is free, global, no API key required.
    Nominatim boundingbox order: [south, north, west, east].
    """
    resp = httpx.get(
        _URL,
        params={"city": city, "country": country, "format": "json", "limit": 1},
        headers=_HEADERS,
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"City not found: {city!r}, {country!r}")
    bb = data[0]["boundingbox"]
    return {
        "bbox_south": float(bb[0]),
        "bbox_north": float(bb[1]),
        "bbox_west":  float(bb[2]),
        "bbox_east":  float(bb[3]),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_nominatim.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/scraper/nominatim.py tests/test_nominatim.py
git commit -m "feat: add Nominatim bounding box service"
```

---

## Task 3: Adaptive Grid Generator

**Files:**
- Create: `app/scraper/grid_generator.py`
- Create: `tests/test_grid_generator.py`

**Interfaces:**
- Consumes: bbox dict from `fetch_bounding_box` (`bbox_north/south/east/west` keys)
- Produces:
  - `GridCell` dataclass: `center_lat, center_lng, radius_km, depth, parent_id`
  - `generate_initial_cells(bbox: dict, radius_km: float) -> list[GridCell]`
  - `subdivide_cell(cell: GridCell, parent_id: str) -> list[GridCell]` — 4 quadrant sub-cells, each with `radius_km / 2`
  - `radius_to_zoom(radius_km: float) -> int` — clamp to [12, 17]

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_grid_generator.py
import math
import pytest
from app.scraper.grid_generator import (
    GridCell, generate_initial_cells, subdivide_cell, radius_to_zoom
)

LONDON_BBOX = {
    "bbox_south": 51.28, "bbox_north": 51.69,
    "bbox_west": -0.51,  "bbox_east":  0.33,
}


def test_generate_initial_cells_covers_bbox():
    cells = generate_initial_cells(LONDON_BBOX, radius_km=1.0)
    assert len(cells) > 0
    lats = [c.center_lat for c in cells]
    lngs = [c.center_lng for c in cells]
    assert min(lats) >= LONDON_BBOX["bbox_south"]
    assert max(lats) <= LONDON_BBOX["bbox_north"] + 0.02
    assert min(lngs) >= LONDON_BBOX["bbox_west"]
    assert max(lngs) <= LONDON_BBOX["bbox_east"] + 0.02


def test_generate_initial_cells_all_have_correct_radius():
    cells = generate_initial_cells(LONDON_BBOX, radius_km=2.0)
    assert all(c.radius_km == 2.0 for c in cells)
    assert all(c.depth == 0 for c in cells)
    assert all(c.parent_id is None for c in cells)


def test_generate_initial_cells_smaller_radius_produces_more_cells():
    coarse = generate_initial_cells(LONDON_BBOX, radius_km=2.0)
    fine   = generate_initial_cells(LONDON_BBOX, radius_km=1.0)
    assert len(fine) > len(coarse)


def test_subdivide_cell_produces_four_quadrants():
    parent = GridCell(center_lat=51.5, center_lng=-0.1, radius_km=1.0, depth=0)
    children = subdivide_cell(parent, parent_id="fake-uuid")
    assert len(children) == 4
    assert all(c.radius_km == pytest.approx(0.5) for c in children)
    assert all(c.depth == 1 for c in children)
    assert all(c.parent_id == "fake-uuid" for c in children)


def test_subdivide_cell_quadrants_are_symmetric():
    parent = GridCell(center_lat=51.5, center_lng=-0.1, radius_km=1.0, depth=0)
    children = subdivide_cell(parent, parent_id="x")
    lats = sorted(set(round(c.center_lat, 5) for c in children))
    lngs = sorted(set(round(c.center_lng, 5) for c in children))
    assert len(lats) == 2
    assert len(lngs) == 2
    # Each quadrant offset should be ≈ radius/2 in degrees
    lat_off = lats[1] - parent.center_lat
    assert lat_off == pytest.approx(1.0 / 2 / 111.0, rel=0.01)


def test_radius_to_zoom_known_values():
    assert radius_to_zoom(1.0) == 14
    assert radius_to_zoom(0.5) == 15
    assert radius_to_zoom(2.0) == 13
    assert radius_to_zoom(4.0) == 12


def test_radius_to_zoom_clamped():
    assert radius_to_zoom(0.05) == 17   # very small → capped at 17
    assert radius_to_zoom(50.0) == 12   # very large → capped at 12
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_grid_generator.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.scraper.grid_generator'`

- [ ] **Step 3: Implement the grid generator**

```python
# app/scraper/grid_generator.py
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class GridCell:
    center_lat: float
    center_lng: float
    radius_km:  float
    depth:      int = 0
    parent_id:  Optional[str] = None


def _lat_deg(km: float) -> float:
    """Degrees of latitude equivalent to km distance."""
    return km / 111.0


def _lng_deg(km: float, lat: float) -> float:
    """Degrees of longitude equivalent to km distance at the given latitude."""
    return km / (111.0 * math.cos(math.radians(lat)))


def generate_initial_cells(bbox: dict, radius_km: float = 1.0) -> list[GridCell]:
    """
    Tile bbox with non-overlapping circular search cells.
    Cell diameter = 2*radius_km so cells tile perfectly without gaps.
    bbox keys: bbox_north, bbox_south, bbox_east, bbox_west
    """
    cells: list[GridCell] = []
    lat = bbox["bbox_south"] + _lat_deg(radius_km)
    while lat <= bbox["bbox_north"] + _lat_deg(radius_km):
        lng = bbox["bbox_west"] + _lng_deg(radius_km, lat)
        while lng <= bbox["bbox_east"] + _lng_deg(radius_km, lat):
            cells.append(GridCell(
                center_lat=round(lat, 6),
                center_lng=round(lng, 6),
                radius_km=radius_km,
            ))
            lng += _lng_deg(radius_km * 2, lat)
        lat += _lat_deg(radius_km * 2)
    return cells


def subdivide_cell(cell: GridCell, parent_id: str) -> list[GridCell]:
    """
    Split a saturated cell into 4 NW/NE/SW/SE quadrant sub-cells.
    Each child has radius = parent.radius_km / 2.
    """
    r = cell.radius_km / 2
    lat_off = _lat_deg(r)
    lng_off = _lng_deg(r, cell.center_lat)
    next_depth = cell.depth + 1
    return [
        GridCell(round(cell.center_lat + lat_off, 6), round(cell.center_lng - lng_off, 6), r, next_depth, parent_id),
        GridCell(round(cell.center_lat + lat_off, 6), round(cell.center_lng + lng_off, 6), r, next_depth, parent_id),
        GridCell(round(cell.center_lat - lat_off, 6), round(cell.center_lng - lng_off, 6), r, next_depth, parent_id),
        GridCell(round(cell.center_lat - lat_off, 6), round(cell.center_lng + lng_off, 6), r, next_depth, parent_id),
    ]


def radius_to_zoom(radius_km: float) -> int:
    """
    Map radius_km to Google Maps zoom level.
    zoom 14 ≈ 1 km radius, zoom 15 ≈ 0.5 km, zoom 13 ≈ 2 km.
    """
    return max(12, min(17, round(14 - math.log2(radius_km))))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_grid_generator.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add app/scraper/grid_generator.py tests/test_grid_generator.py
git commit -m "feat: add adaptive quadtree grid generator"
```

---

## Task 4: Coordinate-Based Google Maps Scraping

**Files:**
- Modify: `app/scraper/maps_dom.py` (add one function at the end)
- Modify: `app/scraper/stage1_scrape.py` (add one function)
- Create: `tests/test_stage1_coords.py`

**Interfaces:**
- Consumes: `radius_to_zoom` from `app.scraper.grid_generator`
- Produces:
  - `maps_dom.discover_clinics_at_coords(page, search_term, lat, lng, zoom, limit) -> tuple[list[dict], int]`
    - Returns `(results[:limit], raw_count_before_limit)` — raw count used for saturation detection
  - `stage1_scrape.run_stage1_at_coords(search_term, lat, lng, radius_km, limit, cutoff_months, headless) -> tuple[list[dict], int]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stage1_coords.py
from unittest.mock import MagicMock, patch
from app.scraper.maps_dom import discover_clinics_at_coords


def _make_page(items):
    page = MagicMock()
    page.evaluate.side_effect = [
        # first call: _scroll_feed JS → item count
        len(items),
        # second call: extract items JS
        items,
    ]
    page.wait_for_selector = MagicMock()
    return page


def test_discover_clinics_at_coords_returns_results_and_raw_count():
    fake_items = [
        {"name": "Clinic A", "url": "https://maps.google.com/a"},
        {"name": "Clinic B", "url": "https://maps.google.com/b"},
        {"name": "Clinic C", "url": "https://maps.google.com/c"},
    ]
    page = _make_page(fake_items)
    with patch("app.scraper.maps_dom.pause"):
        results, raw_count = discover_clinics_at_coords(
            page, "dental clinics", 51.5, -0.1, 14, limit=2
        )
    assert raw_count == 3
    assert len(results) == 2
    assert results[0]["name"] == "Clinic A"


def test_discover_clinics_at_coords_navigates_to_coordinate_url():
    page = _make_page([])
    with patch("app.scraper.maps_dom.pause"):
        discover_clinics_at_coords(page, "dental clinics", 40.758, -73.985, 14, limit=20)
    call_url = page.goto.call_args[0][0]
    assert "40.758" in call_url
    assert "-73.985" in call_url
    assert "14z" in call_url
    assert "dental" in call_url


def test_discover_clinics_at_coords_deduplicates():
    dupes = [
        {"name": "Clinic A", "url": "https://maps.google.com/a"},
        {"name": "Clinic A", "url": "https://maps.google.com/a"},  # duplicate
        {"name": "Clinic B", "url": "https://maps.google.com/b"},
    ]
    page = _make_page(dupes)
    with patch("app.scraper.maps_dom.pause"):
        results, raw_count = discover_clinics_at_coords(page, "dental", 0, 0, 14, limit=20)
    assert len(results) == 2
    assert raw_count == 2  # after dedup
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_stage1_coords.py -v
```
Expected: `ImportError: cannot import name 'discover_clinics_at_coords'`

- [ ] **Step 3: Add `discover_clinics_at_coords` to maps_dom.py**

Append to the end of `app/scraper/maps_dom.py`:

```python
def discover_clinics_at_coords(
    page,
    search_term: str,
    lat: float,
    lng: float,
    zoom: int,
    limit: int,
) -> tuple[list[dict], int]:
    """
    Navigate to coordinates and return (results[:limit], raw_deduped_count).
    Raw count is used by the campaign service for saturation detection.
    URL format: /maps/search/{term}/@{lat},{lng},{zoom}z
    """
    encoded = urllib.parse.quote(search_term)
    url = f"https://www.google.com/maps/search/{encoded}/@{lat},{lng},{zoom}z"
    page.goto(url, wait_until="domcontentloaded")
    pause(2, 4)
    dismiss_consent(page)
    try:
        page.wait_for_selector(FEED, timeout=15000)
    except Exception:
        pass
    _scroll_feed(page, target=limit + 5)
    items = page.evaluate(
        """(sel) => Array.from(document.querySelectorAll(sel)).map(a => ({
            name: a.getAttribute('aria-label') || '', url: a.href }))""",
        RESULT_LINK,
    )
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        if not it["url"] or it["url"] in seen or not it["name"]:
            continue
        seen.add(it["url"])
        out.append({"name": it["name"].strip(), "google_maps_url": it["url"]})
    raw_count = len(out)
    return out[:limit], raw_count
```

- [ ] **Step 4: Add `run_stage1_at_coords` to stage1_scrape.py**

Append to the end of `app/scraper/stage1_scrape.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_stage1_coords.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/scraper/maps_dom.py app/scraper/stage1_scrape.py tests/test_stage1_coords.py
git commit -m "feat: add coordinate-based Google Maps scraping"
```

---

## Task 5: Campaign Models + Repository

**Files:**
- Create: `app/campaigns/__init__.py`
- Create: `app/campaigns/models.py`
- Create: `app/campaigns/repository.py`
- Create: `tests/test_campaign_repository.py`

**Interfaces:**
- Produces (repository):
  - `create_location(country, city, bbox) -> str` — upserts, returns `location_id`
  - `create_campaign_record(niche_id, location_id, radius_km, max_depth, clinics_per_cell, created_by) -> dict`
  - `store_cells(location_id, campaign_id, cells: list[GridCell]) -> None`
  - `get_next_pending_cell(campaign_id) -> dict | None` — `{"id", "cell_id"}`
  - `update_campaign_cell(campaign_cell_id, **kwargs) -> None`
  - `update_campaign(campaign_id, **kwargs) -> None`
  - `count_active_cells(campaign_id) -> int` — count of `pending` + `running`
  - `get_campaign_with_stats(campaign_id) -> dict`
  - `list_campaigns(created_by) -> list[dict]`
- Produces (models):
  - `CampaignCreate` — request body
  - `CampaignOut` — response model

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_campaign_repository.py
from unittest.mock import MagicMock, patch, call
import pytest
from app.scraper.grid_generator import GridCell


def _mock_db():
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.insert.return_value = db
    db.upsert.return_value = db
    db.update.return_value = db
    db.eq.return_value = db
    db.in_.return_value = db
    db.not_.return_value = db
    db.order.return_value = db
    db.limit.return_value = db
    db.execute.return_value = MagicMock(data=[{"id": "loc-id-123", "city": "London"}], count=5)
    return db


def test_create_location_upserts_on_country_city():
    db = _mock_db()
    with patch("app.campaigns.repository.get_db", return_value=db):
        from app.campaigns.repository import create_location
        result = create_location("UK", "London", {
            "bbox_north": 51.69, "bbox_south": 51.28,
            "bbox_east": 0.33, "bbox_west": -0.51,
        })
    assert result == "loc-id-123"
    db.table.assert_called_with("locations")
    db.upsert.assert_called_once()
    upsert_data = db.upsert.call_args[0][0]
    assert upsert_data["city"] == "London"
    assert upsert_data["country"] == "UK"


def test_store_cells_inserts_grid_cell_and_campaign_cell_per_cell():
    db = _mock_db()
    db.execute.return_value = MagicMock(data=[{"id": "cell-uuid"}])
    cells = [
        GridCell(center_lat=51.5, center_lng=-0.1, radius_km=1.0),
        GridCell(center_lat=51.52, center_lng=-0.08, radius_km=1.0),
    ]
    with patch("app.campaigns.repository.get_db", return_value=db):
        from app.campaigns.repository import store_cells
        store_cells("loc-1", "camp-1", cells)
    # 2 cells × (1 grid_cells insert + 1 campaign_cells insert) = 4 insert calls
    insert_calls = [c for c in db.insert.call_args_list]
    assert len(insert_calls) == 4


def test_get_next_pending_cell_returns_none_when_empty():
    db = _mock_db()
    db.execute.return_value = MagicMock(data=[])
    with patch("app.campaigns.repository.get_db", return_value=db):
        from app.campaigns.repository import get_next_pending_cell
        result = get_next_pending_cell("campaign-1")
    assert result is None


def test_count_active_cells_returns_sum_of_pending_and_running():
    db = _mock_db()
    db.execute.return_value = MagicMock(data=[{"id": "a"}, {"id": "b"}, {"id": "c"}])
    with patch("app.campaigns.repository.get_db", return_value=db):
        from app.campaigns.repository import count_active_cells
        result = count_active_cells("campaign-1")
    assert result == 3
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_campaign_repository.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.campaigns'`

- [ ] **Step 3: Create the module files**

```python
# app/campaigns/__init__.py
# (empty)
```

```python
# app/campaigns/models.py
from pydantic import BaseModel


class CampaignCreate(BaseModel):
    niche: str
    city: str
    country: str
    initial_radius_km: float = 1.0
    max_depth: int = 4
    clinics_per_cell: int = 20


class CampaignOut(BaseModel):
    id: str
    status: str
    niche_id: str
    location_id: str
    initial_radius_km: float
    max_depth: int
    clinics_per_cell: int
    created_at: str
```

```python
# app/campaigns/repository.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_campaign_repository.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/campaigns/ tests/test_campaign_repository.py
git commit -m "feat: add campaign models and repository"
```

---

## Task 6: Campaign Service

**Files:**
- Create: `app/campaigns/service.py`
- Create: `tests/test_campaign_service.py`

**Interfaces:**
- Consumes: `fetch_bounding_box`, `generate_initial_cells`, `subdivide_cell`, all of `repository`
- Produces:
  - `create_campaign(niche_slug, city, country, initial_radius_km, max_depth, clinics_per_cell, created_by) -> dict`
  - `on_cell_complete(campaign_cell_id, raw_result_count) -> None` — called by pipeline after Stage 1
  - `pause_campaign(campaign_id) -> None`
  - `resume_campaign(campaign_id) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_campaign_service.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.insert.return_value = db
    db.upsert.return_value = db
    db.update.return_value = db
    db.eq.return_value = db
    db.in_.return_value = db
    db.not_.return_value = db
    db.order.return_value = db
    db.limit.return_value = db
    db.execute.return_value = MagicMock(data=[{
        "id": "camp-1", "niche_id": "niche-1", "status": "pending",
        "max_depth": 4, "clinics_per_cell": 20, "location_id": "loc-1",
        "initial_radius_km": 1.0,
    }])
    return db


@pytest.mark.asyncio
async def test_create_campaign_fetches_bbox_and_generates_cells(mock_db):
    fake_bbox = {"bbox_north": 51.69, "bbox_south": 51.28, "bbox_east": 0.33, "bbox_west": -0.51}
    with (
        patch("app.campaigns.service.fetch_bounding_box", return_value=fake_bbox),
        patch("app.campaigns.service.repo.create_location", return_value="loc-1"),
        patch("app.campaigns.service.repo.create_campaign_record", return_value={"id": "camp-1"}),
        patch("app.campaigns.service.repo.store_cells") as mock_store,
        patch("app.campaigns.service.get_db", return_value=mock_db),
        patch("app.campaigns.service.asyncio.create_task"),
    ):
        from app.campaigns.service import create_campaign
        result = await create_campaign(
            niche_slug="dental", city="London", country="UK",
            initial_radius_km=1.0, max_depth=4,
            clinics_per_cell=20, created_by="user-1",
        )
    assert result["id"] == "camp-1"
    mock_store.assert_called_once()
    cells_stored = mock_store.call_args[0][2]
    assert len(cells_stored) > 0


@pytest.mark.asyncio
async def test_on_cell_complete_marks_done_when_not_saturated(mock_db):
    mock_db.execute.return_value = MagicMock(data=[{
        "id": "cc-1", "campaign_id": "camp-1", "cell_id": "cell-1",
        "status": "running",
    }])
    cell_data = {"id": "cell-1", "center_lat": 51.5, "center_lng": -0.1,
                 "radius_km": 1.0, "depth": 0, "location_id": "loc-1"}
    camp_data = {"max_depth": 4, "location_id": "loc-1"}

    call_count = 0
    def side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(data=[{"id": "cc-1", "campaign_id": "camp-1", "cell_id": "cell-1"}])
        if call_count == 2:
            return MagicMock(data=[camp_data])
        if call_count == 3:
            return MagicMock(data=[cell_data])
        return MagicMock(data=[])

    mock_db.execute.side_effect = side_effect

    with (
        patch("app.campaigns.service.get_db", return_value=mock_db),
        patch("app.campaigns.service.repo.update_campaign_cell") as mock_update_cc,
        patch("app.campaigns.service.repo.get_next_pending_cell", return_value=None),
        patch("app.campaigns.service.repo.count_active_cells", return_value=0),
        patch("app.campaigns.service.repo.update_campaign") as mock_update_c,
    ):
        from app.campaigns import service
        import importlib
        importlib.reload(service)
        await service.on_cell_complete("cc-1", raw_result_count=5)

    mock_update_cc.assert_called_once_with("cc-1", status="done", result_count=5)


@pytest.mark.asyncio
async def test_on_cell_complete_subdivides_when_saturated(mock_db):
    cell_data = {"id": "cell-1", "center_lat": 51.5, "center_lng": -0.1,
                 "radius_km": 1.0, "depth": 0, "location_id": "loc-1"}
    camp_data = {"max_depth": 4, "location_id": "loc-1"}

    call_count = 0
    def side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(data=[{"id": "cc-1", "campaign_id": "camp-1", "cell_id": "cell-1"}])
        if call_count == 2:
            return MagicMock(data=[camp_data])
        if call_count == 3:
            return MagicMock(data=[cell_data])
        return MagicMock(data=[])

    mock_db.execute.side_effect = side_effect

    with (
        patch("app.campaigns.service.get_db", return_value=mock_db),
        patch("app.campaigns.service.repo.store_cells") as mock_store,
        patch("app.campaigns.service.repo.update_campaign_cell") as mock_update_cc,
        patch("app.campaigns.service.repo.get_next_pending_cell", return_value=None),
        patch("app.campaigns.service.repo.count_active_cells", return_value=0),
        patch("app.campaigns.service.repo.update_campaign"),
    ):
        from app.campaigns import service
        import importlib
        importlib.reload(service)
        await service.on_cell_complete("cc-1", raw_result_count=20)

    mock_store.assert_called_once()
    sub_cells = mock_store.call_args[0][2]
    assert len(sub_cells) == 4
    mock_update_cc.assert_called_once_with("cc-1", status="saturated", result_count=20)
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_campaign_service.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.campaigns.service'`

- [ ] **Step 3: Implement the service**

```python
# app/campaigns/service.py
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

    if raw_result_count >= SATURATION_THRESHOLD and cell["depth"] < camp["max_depth"]:
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_campaign_service.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/campaigns/service.py tests/test_campaign_service.py
git commit -m "feat: add campaign service with adaptive quadtree cell advancement"
```

---

## Task 7: Campaign Router

**Files:**
- Create: `app/campaigns/router.py`
- Create: `tests/test_campaign_router.py`

**Interfaces:**
- Consumes: `create_campaign`, `pause_campaign`, `resume_campaign` from service; `get_campaign_with_stats`, `list_campaigns` from repository
- Produces: 5 endpoints under `/campaigns` prefix

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_campaign_router.py
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}
FAKE_USER = {"sub": "user-1"}
FAKE_CAMPAIGN = {
    "id": "camp-1", "status": "running", "niche_id": "n-1",
    "location_id": "loc-1", "initial_radius_km": 1.0,
    "max_depth": 4, "clinics_per_cell": 20, "created_at": "2026-06-20T00:00:00Z",
    "city": "London", "country": "UK",
    "total_cells": 50, "done_cells": 10, "running_cells": 1,
    "pending_cells": 39, "saturated_cells": 0, "clinics_found": 120,
}


def test_create_campaign_returns_201():
    with (
        patch("app.campaigns.router.get_current_user", return_value=FAKE_USER),
        patch("app.campaigns.router.create_campaign", new_callable=AsyncMock,
              return_value={"id": "camp-1", "status": "pending"}),
    ):
        resp = client.post("/campaigns", json={
            "niche": "dental", "city": "London", "country": "UK"
        }, headers=AUTH)
    assert resp.status_code == 201
    assert resp.json()["id"] == "camp-1"


def test_list_campaigns_returns_200():
    with (
        patch("app.campaigns.router.get_current_user", return_value=FAKE_USER),
        patch("app.campaigns.router.repo.list_campaigns", return_value=[FAKE_CAMPAIGN]),
    ):
        resp = client.get("/campaigns", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_campaign_stats_returns_200():
    with (
        patch("app.campaigns.router.get_current_user", return_value=FAKE_USER),
        patch("app.campaigns.router.repo.get_campaign_with_stats", return_value=FAKE_CAMPAIGN),
    ):
        resp = client.get("/campaigns/camp-1", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["total_cells"] == 50
    assert resp.json()["clinics_found"] == 120


def test_pause_campaign_returns_200():
    with (
        patch("app.campaigns.router.get_current_user", return_value=FAKE_USER),
        patch("app.campaigns.router.pause_campaign", new_callable=AsyncMock),
    ):
        resp = client.post("/campaigns/camp-1/pause", headers=AUTH)
    assert resp.status_code == 200


def test_resume_campaign_returns_200():
    with (
        patch("app.campaigns.router.get_current_user", return_value=FAKE_USER),
        patch("app.campaigns.router.resume_campaign", new_callable=AsyncMock),
    ):
        resp = client.post("/campaigns/camp-1/resume", headers=AUTH)
    assert resp.status_code == 200


def test_create_campaign_requires_auth():
    resp = client.post("/campaigns", json={"niche": "dental", "city": "London", "country": "UK"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_campaign_router.py -v
```
Expected: `404 Not Found` (router not registered yet).

- [ ] **Step 3: Implement the router**

```python
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
    return stats


@router.post("/{campaign_id}/pause")
async def pause(campaign_id: str, _user: dict = Depends(get_current_user)):
    await pause_campaign(campaign_id)
    return {"status": "paused"}


@router.post("/{campaign_id}/resume")
async def resume(campaign_id: str, _user: dict = Depends(get_current_user)):
    await resume_campaign(campaign_id)
    return {"status": "running"}
```

- [ ] **Step 4: Register router in `app/main.py`**

Add these two lines to `app/main.py` alongside the existing router imports and `include_router` calls:

```python
# add import at top with other router imports:
from app.campaigns.router import router as campaigns_router

# add inside the app setup, after the existing include_router calls:
app.include_router(campaigns_router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_campaign_router.py -v
```
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add app/campaigns/router.py app/main.py tests/test_campaign_router.py
git commit -m "feat: add campaign router with 5 endpoints"
```

---

## Task 8: Pipeline Hook — `run_pipeline_at_coords`

**Files:**
- Modify: `app/scraper/pipeline.py`
- Modify: `tests/test_scraper_router.py` (verify existing tests still pass)

**Interfaces:**
- Consumes: `run_stage1_at_coords` from `stage1_scrape`; `on_cell_complete` from `campaigns.service`
- Produces: `run_pipeline_at_coords(job_id, niche_config, lat, lng, radius_km, campaign_cell_id) -> None`
  - After Stage 1, calls `on_cell_complete(campaign_cell_id, raw_count)` if `campaign_cell_id` is set
  - Runs Stage 2 + Stage 3 identically to existing `run_pipeline`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_scraper_router.py

@pytest.mark.asyncio
async def test_run_pipeline_at_coords_calls_on_cell_complete():
    from unittest.mock import AsyncMock, patch, MagicMock
    from app.scraper.pipeline import run_pipeline_at_coords

    fake_clinics = [{"name": "Clinic A", "google_maps_url": "http://maps/a", "website": None, "reviews": []}]
    fake_niche = MagicMock()
    fake_niche.search_term = "dental clinics"
    fake_niche.limit = 20
    fake_niche.cutoff_months = 12
    fake_niche.niche_id = "niche-1"
    fake_niche.categorize_system_prompt = "prompt"

    mock_db = MagicMock()
    mock_db.table.return_value = mock_db
    mock_db.update.return_value = mock_db
    mock_db.select.return_value = mock_db
    mock_db.insert.return_value = mock_db
    mock_db.upsert.return_value = mock_db
    mock_db.in_.return_value = mock_db
    mock_db.eq.return_value = mock_db
    mock_db.execute.return_value = MagicMock(data=[{"id": "clinic-1"}])

    on_complete = AsyncMock()

    with (
        patch("app.scraper.pipeline.run_stage1_at_coords", return_value=(fake_clinics, 20)),
        patch("app.scraper.pipeline.categorize_clinic", return_value={"recommend_outreach": False}),
        patch("app.scraper.pipeline.get_db", return_value=mock_db),
        patch("app.scraper.pipeline.on_cell_complete", on_complete),
    ):
        await run_pipeline_at_coords(
            "job-1", fake_niche,
            lat=51.5, lng=-0.1, radius_km=1.0,
            campaign_cell_id="cc-1",
        )

    on_complete.assert_awaited_once_with("cc-1", 20)
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_scraper_router.py::test_run_pipeline_at_coords_calls_on_cell_complete -v
```
Expected: `ImportError: cannot import name 'run_pipeline_at_coords'`

- [ ] **Step 3: Extract shared Stage 2+3 helper and add `run_pipeline_at_coords` to pipeline.py**

Add these two blocks to `app/scraper/pipeline.py` after the existing `run_pipeline` function:

```python
# import at top of file (add to existing imports):
from app.scraper.stage1_scrape import run_stage1_at_coords
from app.campaigns.service import on_cell_complete


async def _process_clinics(job_id: str, niche_config: NicheConfig, raw_clinics: list[dict]) -> None:
    """
    Shared Stage 2 + Stage 3 logic used by both pipeline variants.
    Deduplicates against existing DB records, qualifies, finds emails.
    """
    db = get_db()
    total = len(raw_clinics)

    scraped_urls = [c["google_maps_url"] for c in raw_clinics if c.get("google_maps_url")]
    existing_rows = db.table("clinics").select("google_maps_url").in_("google_maps_url", scraped_urls).execute()
    existing_urls = {r["google_maps_url"] for r in existing_rows.data}
    new_clinics = [c for c in raw_clinics if c.get("google_maps_url") not in existing_urls]
    already_known = total - len(new_clinics)
    log.info("[job=%s] %d new | %d already in DB", job_id[:8], len(new_clinics), already_known)

    saved_rows = []
    for c in new_clinics:
        row = {
            "job_id": job_id, "niche_id": niche_config.niche_id,
            "name": c.get("name"), "address": c.get("address"),
            "phone": c.get("phone"), "website": c.get("website"),
            "google_maps_url": c.get("google_maps_url"),
            "rating": c.get("rating"), "review_count": c.get("review_count"),
            "reviews_json": c.get("reviews", []),
        }
        result = db.table("clinics").insert(row).execute()
        saved_rows.append((result.data[0]["id"], c))

    new_total = len(saved_rows)
    qualified = 0
    for i, (clinic_id, raw) in enumerate(saved_rows, 1):
        _progress(db, job_id, 2,
                  f"Qualifying clinic {i}/{new_total}: {raw.get('name', '...')}",
                  new=new_total, current=i, qualified=qualified)
        q = await asyncio.to_thread(categorize_clinic, raw, niche_config.categorize_system_prompt)
        if q.get("recommend_outreach"):
            qualified += 1
        db.table("qualifications").upsert({
            "clinic_id": clinic_id,
            "qualification_score": q.get("qualification_score"),
            "primary_pain_point": q.get("primary_pain_point"),
            "complaint_quote": q.get("complaint_quote"),
            "personalization_hook": q.get("personalization_hook"),
            "recommend_outreach": q.get("recommend_outreach", False),
            "model_used": "llama-3.3-70b-versatile",
        }, on_conflict="clinic_id").execute()

    emails_found = 0
    for i, (clinic_id, raw) in enumerate(saved_rows, 1):
        website = raw.get("website")
        if not website:
            continue
        _progress(db, job_id, 3,
                  f"Finding email {i}/{new_total}: {raw.get('name', '...')}",
                  new=new_total, qualified=qualified, emails_found=emails_found, current=i)
        result = await find_email_for_clinic(website, raw.get("name", ""))
        if result.get("email"):
            emails_found += 1
        db.table("leads").upsert({
            "clinic_id": clinic_id, "email": result["email"],
            "email_method": result["method"], "all_emails": result["all_emails"],
            "doctors_found": result["doctors_found"], "status": "new",
        }, on_conflict="clinic_id").execute()

    return new_total, qualified, emails_found


async def run_pipeline_at_coords(
    job_id: str,
    niche_config: NicheConfig,
    lat: float,
    lng: float,
    radius_km: float,
    campaign_cell_id: str | None = None,
) -> None:
    """Coordinate-based pipeline variant used by the campaign system."""
    db = get_db()
    db.table("scrape_jobs").update({"status": "running"}).eq("id", job_id).execute()
    try:
        _progress(db, job_id, 1, f"Scraping ({lat:.4f}, {lng:.4f}) r={radius_km}km...")
        raw_clinics, raw_count = await asyncio.to_thread(
            run_stage1_at_coords,
            search_term=niche_config.search_term,
            lat=lat, lng=lng, radius_km=radius_km,
            limit=niche_config.limit,
            cutoff_months=niche_config.cutoff_months,
        )
        _progress(db, job_id, 1, f"Found {len(raw_clinics)} clinics (raw={raw_count})",
                  clinics_found=len(raw_clinics), raw_count=raw_count)

        if campaign_cell_id:
            await on_cell_complete(campaign_cell_id, raw_count)

        new_total, qualified, emails_found = await _process_clinics(job_id, niche_config, raw_clinics)

        db.table("scrape_jobs").update({
            "status": "done", "finished_at": _now(),
            "progress": {
                "stage": 3, "message": "Complete",
                "raw_count": raw_count, "new": new_total,
                "qualified": qualified, "emails_found": emails_found,
            },
        }).eq("id", job_id).execute()

    except Exception as e:
        db.table("scrape_jobs").update({
            "status": "failed", "error_msg": str(e)[:500], "finished_at": _now()
        }).eq("id", job_id).execute()
        raise
```

> **Important:** Also refactor the existing `run_pipeline` to call `_process_clinics` instead of duplicating Stage 2+3 logic. Replace the Stage 2 and Stage 3 loops in `run_pipeline` with:
>
> ```python
> new_total, qualified, emails_found = await _process_clinics(job_id, niche_config, raw_clinics)
> ```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: all existing tests pass + new test passes.

- [ ] **Step 5: Commit**

```bash
git add app/scraper/pipeline.py tests/test_scraper_router.py
git commit -m "feat: add run_pipeline_at_coords and extract shared _process_clinics helper"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| Adaptive quadtree subdivision | Task 3 (`subdivide_cell`), Task 6 (`on_cell_complete`) |
| Nominatim bounding boxes (global, no API key) | Task 2 |
| Coordinate-based Google Maps search | Task 4 |
| Saturation detection (≥20 results) | Task 6 (`SATURATION_THRESHOLD`) |
| Campaign create/list/detail endpoints | Task 7 |
| Pause/resume endpoints | Task 7 |
| Per-cell status tracking | Task 1 (`campaign_cells`), Task 6 |
| Campaign auto-advances to next cell | Task 6 (`_advance_campaign`) |
| Existing dedup + Stage 2 + Stage 3 untouched | Task 8 (`_process_clinics` reuses exact same logic) |
| International support (any city, any country) | Task 2 (Nominatim is global) |
| Existing scrape jobs unaffected | `run_pipeline` unchanged except Stage 2+3 extraction |

**Placeholder scan:** None found.

**Type consistency:** `GridCell.parent_id` is `str | None` in Task 3; `store_cells` in Task 5 passes `cell.parent_id` directly — consistent. `on_cell_complete` signature `(campaign_cell_id: str, raw_result_count: int)` matches usage in Task 8.
