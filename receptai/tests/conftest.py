import os
import pytest
from unittest.mock import MagicMock, patch as mock_patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-minimum-32-chars!")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("RESEND_API_KEY", "re_test_key")
os.environ.setdefault("FROM_EMAIL", "test@receptai.com")


@pytest.fixture(autouse=True, scope="session")
def patch_database_get_db():
    """Patch app.database.get_db so the lifespan health check doesn't call real Supabase."""
    mock_db = MagicMock()
    # Make .table("niches").select("id").limit(1).execute() return successfully
    mock_db.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[{"id": "niche-id"}])
    with mock_patch("app.database.get_db", return_value=mock_db):
        yield mock_db


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear the in-memory rate-limit counters before each test so tests don't bleed into each other."""
    from app.core.rate_limit import limiter
    limiter.reset()
    yield
