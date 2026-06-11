import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, patch


def _make_auth_db():
    profiles = {}
    def table(name):
        t = MagicMock()
        def sel(*a):
            s = MagicMock()
            def eq(c, v):
                e = MagicMock()
                def exe():
                    r = MagicMock()
                    r.data = [p for p in profiles.values() if p.get(c) == v]
                    return r
                e.execute = exe; return e
            s.eq = eq; return s
        def ins(row):
            i = MagicMock()
            def exe():
                profiles[row["id"]] = row
                r = MagicMock(); r.data = [row]; return r
            i.execute = exe; return i
        t.select = sel; t.insert = ins; return t
    db = MagicMock(); db.table = table; return db


def _make_leads_db(leads_data=None):
    leads = leads_data or {}
    def table(name):
        t = MagicMock()
        def sel(*a):
            s = MagicMock()
            def eq(c, v):
                e = MagicMock()
                def exe():
                    r = MagicMock()
                    r.data = [l for l in leads.values() if l.get(c) == v]
                    return r
                e.execute = exe; return e
            s.eq = eq
            # for list_leads — no eq called
            def exe_all():
                r = MagicMock(); r.data = list(leads.values()); return r
            s.execute = exe_all
            return s
        def upd(data):
            u = MagicMock()
            def eq(c, v):
                e = MagicMock()
                def exe():
                    r = MagicMock()
                    updated = [l for l in leads.values() if l.get(c) == v]
                    if updated:
                        for l in updated:
                            l.update(data)
                    r.data = updated
                    return r
                e.execute = exe; return e
            u.eq = eq; return u
        t.select = sel; t.update = upd; return t
    db = MagicMock(); db.table = table; return db


@pytest.fixture
async def auth_headers():
    from app.main import app
    with patch("app.auth.service.get_db", return_value=_make_auth_db()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/auth/register", json={"email": "leads@test.com", "password": "Pass123!"})
            r = await c.post("/auth/token", data={"username": "leads@test.com", "password": "Pass123!"})
            return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_list_leads_returns_list(auth_headers):
    from app.main import app
    with patch("app.leads.repository.get_db", return_value=_make_leads_db()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.headers.update(auth_headers)
            r = await c.get("/leads/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_patch_nonexistent_lead_returns_404(auth_headers):
    from app.main import app
    with patch("app.leads.repository.get_db", return_value=_make_leads_db()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.headers.update(auth_headers)
            r = await c.patch("/leads/00000000-0000-0000-0000-000000000000",
                              json={"status": "contacted"})
    assert r.status_code == 404


async def test_patch_invalid_status_returns_400(auth_headers):
    from app.main import app
    with patch("app.leads.repository.get_db", return_value=_make_leads_db()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.headers.update(auth_headers)
            r = await c.patch("/leads/any-id", json={"status": "banana"})
    assert r.status_code == 400


async def test_get_lead_not_found_returns_404(auth_headers):
    from app.main import app
    with patch("app.leads.repository.get_db", return_value=_make_leads_db()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.headers.update(auth_headers)
            r = await c.get("/leads/nonexistent-id")
    assert r.status_code == 404
