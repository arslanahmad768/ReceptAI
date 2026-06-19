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
