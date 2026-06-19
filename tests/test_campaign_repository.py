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
