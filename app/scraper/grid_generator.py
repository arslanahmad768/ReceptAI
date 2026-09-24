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
