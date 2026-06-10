import os
import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-minimum-32-chars!")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("RESEND_API_KEY", "re_test_key")
os.environ.setdefault("FROM_EMAIL", "test@receptai.com")


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear the in-memory rate-limit counters before each test so tests don't bleed into each other."""
    from app.core.rate_limit import limiter
    limiter.reset()
    yield
