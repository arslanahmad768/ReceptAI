import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_db_scraper():
    """Mock that supports scrape_jobs and niches table operations."""
    jobs = {}
    niches = {"dental": {"id": "niche-dental-id", "slug": "dental"}}

    def table_side(name):
        t = MagicMock()
        if name == "niches":
            def sel(*a):
                s = MagicMock()
                def eq(col, val):
                    e = MagicMock()
                    def exe():
                        r = MagicMock()
                        r.data = [v for v in niches.values() if v.get(col) == val]
                        return r
                    e.execute = exe
                    return e
                s.eq = eq
                return s
            t.select = sel
        elif name == "scrape_jobs":
            def ins(row):
                i = MagicMock()
                def exe():
                    jobs[row["id"]] = row
                    r = MagicMock(); r.data = [row]; return r
                i.execute = exe
                return i
            def sel(*a):
                s = MagicMock()
                def eq(col, val):
                    e = MagicMock()
                    def exe():
                        r = MagicMock()
                        r.data = [j for j in jobs.values() if j.get(col) == val]
                        return r
                    e.execute = exe
                    return e
                s.eq = eq
                return s
            def order(*a, **kw):
                o = MagicMock()
                def lim(n):
                    l = MagicMock()
                    def exe():
                        r = MagicMock(); r.data = list(jobs.values()); return r
                    l.execute = exe
                    return l
                o.limit = lim
                return o
            t.insert = ins
            t.select = sel
            t.order = order
        return t

    db = MagicMock()
    db.table = table_side
    return db


@pytest.fixture
async def auth_token(mock_db_scraper):
    from app.main import app
    with patch("app.auth.service.get_db", return_value=_make_auth_mock_db()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/auth/register", json={"email": "scraper@test.com", "password": "Pass123!"})
            r = await c.post("/auth/token", data={"username": "scraper@test.com", "password": "Pass123!"})
            return r.json()["access_token"]


def _make_auth_mock_db():
    """Minimal auth mock (same pattern as test_auth.py)."""
    profiles = {}
    def table_side(name):
        t = MagicMock()
        def sel(*a):
            s = MagicMock()
            def eq(col, val):
                e = MagicMock()
                def exe():
                    r = MagicMock()
                    r.data = [p for p in profiles.values() if p.get(col) == val]
                    return r
                e.execute = exe
                return e
            s.eq = eq
            return s
        def ins(row):
            i = MagicMock()
            def exe():
                profiles[row["id"]] = row
                r = MagicMock(); r.data = [row]; return r
            i.execute = exe
            return i
        t.select = sel; t.insert = ins
        return t
    db = MagicMock(); db.table = table_side; return db


async def test_create_job_without_auth_returns_401():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/scraper/jobs", json={"niche": "dental", "location": "Tampa FL"})
    assert r.status_code == 401


async def test_create_job_unknown_niche_returns_400():
    from app.main import app
    auth_db = _make_auth_mock_db()
    with patch("app.auth.service.get_db", return_value=auth_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/auth/register", json={"email": "s2@test.com", "password": "Pass123!"})
            r2 = await c.post("/auth/token", data={"username": "s2@test.com", "password": "Pass123!"})
            token = r2.json()["access_token"]
            with patch("app.scraper.router.get_db", return_value=_make_auth_mock_db()):
                r = await c.post("/scraper/jobs",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"niche": "plumbing", "location": "Tampa FL"})
    assert r.status_code == 400


async def test_create_job_returns_job_id():
    from app.main import app
    auth_db = _make_auth_mock_db()
    scraper_db = MagicMock()
    niche_result = MagicMock(); niche_result.data = [{"id": "niche-id"}]
    niche_q = MagicMock(); niche_q.execute.return_value = niche_result
    niche_sel = MagicMock(); niche_sel.eq.return_value = niche_q
    insert_result = MagicMock(); insert_result.data = [{"id": "job-id"}]
    insert_mock = MagicMock(); insert_mock.execute.return_value = insert_result
    scraper_db.table.return_value.select.return_value = niche_sel
    scraper_db.table.return_value.insert.return_value = insert_mock

    with patch("app.auth.service.get_db", return_value=auth_db), \
         patch("app.scraper.router.get_db", return_value=scraper_db), \
         patch("app.scraper.router.asyncio.create_task"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/auth/register", json={"email": "s3@test.com", "password": "Pass123!"})
            r2 = await c.post("/auth/token", data={"username": "s3@test.com", "password": "Pass123!"})
            token = r2.json()["access_token"]
            r = await c.post("/scraper/jobs",
                headers={"Authorization": f"Bearer {token}"},
                json={"niche": "dental", "location": "Tampa FL", "limit": 5})
    assert r.status_code == 201
    assert "job_id" in r.json()


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
