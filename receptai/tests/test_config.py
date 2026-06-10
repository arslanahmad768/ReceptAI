import pytest
from app.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-32-chars-longggggggg")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service")
    monkeypatch.setenv("GROQ_API_KEY", "groq")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("FROM_EMAIL", "test@example.com")
    s = Settings(_env_file=None)
    assert s.secret_key == "test-secret-32-chars-longggggggg"
    assert s.from_email == "test@example.com"


def test_origins_list_splits_correctly(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-32-chars-longggggggg")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service")
    monkeypatch.setenv("GROQ_API_KEY", "groq")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("FROM_EMAIL", "test@example.com")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000,https://app.receptai.com")
    s = Settings(_env_file=None)
    assert len(s.origins_list) == 2
    assert "https://app.receptai.com" in s.origins_list


def test_secret_key_too_short_raises(monkeypatch):
    for key in ["SECRET_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY",
                "SUPABASE_SERVICE_ROLE_KEY", "GROQ_API_KEY", "RESEND_API_KEY", "FROM_EMAIL"]:
        monkeypatch.setenv(key, {"SECRET_KEY": "short"}.get(key, "test"))
    monkeypatch.setenv("SECRET_KEY", "tooshort")
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_origins_list_ignores_trailing_comma(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-32-chars-longggggggg")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service")
    monkeypatch.setenv("GROQ_API_KEY", "groq")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("FROM_EMAIL", "test@example.com")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000,")
    s = Settings(_env_file=None)
    assert "" not in s.origins_list
    assert len(s.origins_list) == 1
