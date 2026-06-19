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
        await service.on_cell_complete("cc-1", raw_result_count=20)

    mock_store.assert_called_once()
    sub_cells = mock_store.call_args[0][2]
    assert len(sub_cells) == 4
    mock_update_cc.assert_called_once_with("cc-1", status="saturated", result_count=20)
