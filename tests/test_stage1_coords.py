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
