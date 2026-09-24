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
