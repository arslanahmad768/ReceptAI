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
