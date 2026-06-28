import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, patch

# Ensure submodules are importable before patch() resolves them
import app.cron.router  # noqa: F401
import app.cron.repository  # noqa: F401


@pytest.fixture(autouse=True)
def reset_scheduler():
    import app.cron.scheduler as sched_mod
    yield
    if sched_mod._scheduler is not None and sched_mod._scheduler.running:
        sched_mod._scheduler.shutdown(wait=False)
    sched_mod._scheduler = None


def _make_cron_mock_db():
    profiles = {}
    niches_store = {"dental": {"id": "niche-dental-id", "slug": "dental"}}
    cron_jobs_store = {}

    def make_db():
        mock_db = MagicMock()

        def table(name):
            t = MagicMock()

            def select(*args):
                s = MagicMock()
                def eq(col, val):
                    e = MagicMock()
                    def execute():
                        r = MagicMock()
                        if name == "profiles":
                            r.data = [p for p in profiles.values() if p.get(col) == val]
                        elif name == "niches":
                            r.data = [v for v in niches_store.values() if v.get(col) == val]
                        elif name == "cron_jobs":
                            r.data = [j for j in cron_jobs_store.values() if j.get(col) == val]
                        else:
                            r.data = []
                        return r
                    e.execute = execute
                    e.eq = lambda c2, v2: e
                    return e
                s.eq = eq
                return s

            def insert(row):
                i = MagicMock()
                def execute():
                    if name == "profiles":
                        profiles[row["id"]] = row
                    elif name == "cron_jobs":
                        cron_jobs_store[row["id"]] = row
                    r = MagicMock()
                    r.data = [row]
                    return r
                i.execute = execute
                return i

            def delete():
                d = MagicMock()
                def eq(col, val):
                    e = MagicMock()
                    def execute():
                        r = MagicMock()
                        if name == "cron_jobs":
                            matching = [j for j in cron_jobs_store.values() if j.get(col) == val]
                            for job in matching:
                                del cron_jobs_store[job["id"]]
                            r.data = matching
                        else:
                            r.data = []
                        return r
                    e.execute = execute
                    return e
                d.eq = eq
                return d

            t.select = select
            t.insert = insert
            t.delete = delete
            t.update = MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(execute=MagicMock()))
            ))
            return t

        mock_db.table = table
        return mock_db

    return make_db()


@pytest.fixture
def cron_mock_db():
    db = _make_cron_mock_db()
    with patch("app.auth.service.get_db", return_value=db), \
         patch("app.cron.repository.get_db", return_value=db), \
         patch("app.cron.router.get_db", return_value=db):
        yield db


@pytest.fixture
async def auth_headers(cron_mock_db):
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/auth/register", json={"email": "cron@test.com", "password": "Pass123!"})
        r = await c.post("/auth/token", data={"username": "cron@test.com", "password": "Pass123!"})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_create_cron_job(auth_headers, cron_mock_db):
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers.update(auth_headers)
        with patch("app.auth.service.get_db", return_value=cron_mock_db), \
             patch("app.cron.repository.get_db", return_value=cron_mock_db), \
             patch("app.cron.router.get_db", return_value=cron_mock_db):
            r = await c.post("/cron/jobs", json={
                "name": "Weekly Tampa Dental",
                "niche": "dental",
                "location": "Tampa FL",
                "limit_count": 20,
                "cron_expr": "0 9 * * 1"
            })
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
        data = r.json()
        assert data["cron_expr"] == "0 9 * * 1"
        assert data["is_active"] is True


async def test_list_cron_jobs(auth_headers, cron_mock_db):
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers.update(auth_headers)
        with patch("app.auth.service.get_db", return_value=cron_mock_db), \
             patch("app.cron.repository.get_db", return_value=cron_mock_db), \
             patch("app.cron.router.get_db", return_value=cron_mock_db):
            r = await c.get("/cron/jobs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


async def test_delete_cron_job(auth_headers, cron_mock_db):
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers.update(auth_headers)
        with patch("app.auth.service.get_db", return_value=cron_mock_db), \
             patch("app.cron.repository.get_db", return_value=cron_mock_db), \
             patch("app.cron.router.get_db", return_value=cron_mock_db):
            r = await c.post("/cron/jobs", json={
                "name": "To Delete", "niche": "dental",
                "location": "Miami FL", "cron_expr": "0 8 * * 2"
            })
            job_id = r.json()["id"]
            r = await c.delete(f"/cron/jobs/{job_id}")
        assert r.status_code == 204
