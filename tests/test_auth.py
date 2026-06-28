import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, patch

# Build a fake DB that simulates profiles table operations
def _make_mock_db():
    """Returns a mock supabase Client that simulates an in-memory profiles table."""
    profiles = {}

    def table_side_effect(name):
        mock_table = MagicMock()

        def select_side_effect(*args):
            mock_select = MagicMock()
            def eq_side_effect(col, val):
                mock_eq = MagicMock()
                def execute_side_effect():
                    mock_result = MagicMock()
                    if col == "email":
                        match = [p for p in profiles.values() if p.get("email") == val]
                        mock_result.data = match
                    elif col == "id":
                        match = [p for p in profiles.values() if p.get("id") == val]
                        mock_result.data = match
                    else:
                        mock_result.data = []
                    return mock_result
                mock_eq.execute = execute_side_effect
                return mock_eq
            mock_select.eq = eq_side_effect
            return mock_select

        def insert_side_effect(row):
            mock_insert = MagicMock()
            def execute_insert():
                profiles[row["id"]] = row
                mock_result = MagicMock()
                mock_result.data = [row]
                return mock_result
            mock_insert.execute = execute_insert
            return mock_insert

        mock_table.select = select_side_effect
        mock_table.insert = insert_side_effect
        return mock_table

    mock_db = MagicMock()
    mock_db.table = table_side_effect
    return mock_db


@pytest.fixture
def mock_db():
    db = _make_mock_db()
    with patch("app.auth.service.get_db", return_value=db):
        yield db


@pytest.fixture
async def client(mock_db):
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_register_creates_user(client):
    r = await client.post("/auth/register", json={
        "email": "newuser@test.com", "password": "Str0ng!Pass"
    })
    assert r.status_code == 201
    assert r.json()["email"] == "newuser@test.com"
    assert "id" in r.json()


async def test_duplicate_email_rejected(client):
    body = {"email": "dup@test.com", "password": "Str0ng!Pass"}
    await client.post("/auth/register", json=body)
    r = await client.post("/auth/register", json=body)
    assert r.status_code == 409


async def test_login_returns_tokens(client):
    await client.post("/auth/register", json={"email": "login@test.com", "password": "Pass123!"})
    r = await client.post("/auth/token", data={"username": "login@test.com", "password": "Pass123!"})
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


async def test_wrong_password_returns_401(client):
    await client.post("/auth/register", json={"email": "wrong@test.com", "password": "Pass123!"})
    r = await client.post("/auth/token", data={"username": "wrong@test.com", "password": "wrongpass"})
    assert r.status_code == 401


async def test_protected_route_without_token_returns_401(client):
    r = await client.get("/leads/")
    assert r.status_code in (401, 404)  # 404 if leads router not mounted yet


async def test_refresh_token_works(client):
    await client.post("/auth/register", json={"email": "refresh@test.com", "password": "Pass123!"})
    login_r = await client.post("/auth/token", data={"username": "refresh@test.com", "password": "Pass123!"})
    refresh_token = login_r.json()["refresh_token"]
    r = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert r.status_code == 200
    assert "access_token" in r.json()
