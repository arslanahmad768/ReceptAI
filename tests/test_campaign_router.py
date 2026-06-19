# tests/test_campaign_router.py
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.auth.dependencies import get_current_user

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}
FAKE_USER = {"sub": "user-1"}
FAKE_CAMPAIGN = {
    "id": "camp-1", "status": "running", "niche_id": "n-1",
    "location_id": "loc-1", "initial_radius_km": 1.0,
    "max_depth": 4, "clinics_per_cell": 20, "created_at": "2026-06-20T00:00:00Z",
    "city": "London", "country": "UK", "created_by": "user-1",
    "total_cells": 50, "done_cells": 10, "running_cells": 1,
    "pending_cells": 39, "saturated_cells": 0, "clinics_found": 120,
}


def test_create_campaign_returns_201():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    try:
        with patch("app.campaigns.router.create_campaign", new_callable=AsyncMock,
                   return_value={"id": "camp-1", "status": "pending"}):
            resp = client.post("/campaigns", json={
                "niche": "dental", "city": "London", "country": "UK"
            }, headers=AUTH)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 201
    assert resp.json()["id"] == "camp-1"


def test_list_campaigns_returns_200():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    try:
        with patch("app.campaigns.router.repo.list_campaigns", return_value=[FAKE_CAMPAIGN]):
            resp = client.get("/campaigns", headers=AUTH)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_campaign_stats_returns_200():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    try:
        with patch("app.campaigns.router.repo.get_campaign_with_stats", return_value=FAKE_CAMPAIGN):
            resp = client.get("/campaigns/camp-1", headers=AUTH)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200
    assert resp.json()["total_cells"] == 50
    assert resp.json()["clinics_found"] == 120


def test_pause_campaign_returns_200():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    try:
        with patch("app.campaigns.router.repo.get_campaign_with_stats", return_value=FAKE_CAMPAIGN), \
             patch("app.campaigns.router.pause_campaign", new_callable=AsyncMock):
            resp = client.post("/campaigns/camp-1/pause", headers=AUTH)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200


def test_resume_campaign_returns_200():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    try:
        with patch("app.campaigns.router.repo.get_campaign_with_stats", return_value=FAKE_CAMPAIGN), \
             patch("app.campaigns.router.resume_campaign", new_callable=AsyncMock):
            resp = client.post("/campaigns/camp-1/resume", headers=AUTH)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200


def test_create_campaign_requires_auth():
    # No dependency override — real auth runs and must reject the unauthenticated request
    resp = client.post("/campaigns", json={"niche": "dental", "city": "London", "country": "UK"})
    assert resp.status_code == 401
