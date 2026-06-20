import pytest
from unittest.mock import patch, MagicMock


# ── Quality scoring ───────────────────────────────────────────────────────────

def test_email_priority_score_dr_prefix():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("drsmith@clinic.com") == 100

def test_email_priority_score_dr_dot_prefix():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("dr.jones@clinic.com") == 100

def test_email_priority_score_info():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("info@clinic.com") == 90

def test_email_priority_score_office():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("office@clinic.com") == 90

def test_email_priority_score_frontdesk():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("frontdesk@clinic.com") == 85

def test_email_priority_score_webmaster_is_zero():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("webmaster@clinic.com") == 0

def test_email_priority_score_noreply_is_zero():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("noreply@clinic.com") == 0

def test_email_priority_score_unknown_prefix_returns_70():
    from app.scraper.stage3_emails import _email_priority_score
    # Unknown prefix treated as a personal name — high value
    assert _email_priority_score("jsmith@clinic.com") == 70

def test_best_email_picks_dr_over_info():
    from app.scraper.stage3_emails import _best_email
    emails = ["info@clinic.com", "drjones@clinic.com", "webmaster@clinic.com"]
    assert _best_email(emails) == "drjones@clinic.com"

def test_best_email_picks_info_over_webmaster():
    from app.scraper.stage3_emails import _best_email
    emails = ["webmaster@clinic.com", "info@clinic.com"]
    assert _best_email(emails) == "info@clinic.com"

def test_best_email_returns_none_for_empty_list():
    from app.scraper.stage3_emails import _best_email
    assert _best_email([]) is None

def test_best_email_returns_single():
    from app.scraper.stage3_emails import _best_email
    assert _best_email(["contact@clinic.com"]) == "contact@clinic.com"


# ── Snov.io ───────────────────────────────────────────────────────────────────

def test_snov_lookup_skipped_when_no_credentials():
    from app.scraper.stage3_emails import snov_lookup
    result = snov_lookup("example.com")
    assert result["email"] is None
    assert result["method"] == "snov_skipped"


def test_snov_lookup_returns_best_email(monkeypatch):
    from app.scraper.stage3_emails import snov_lookup
    from app.scraper import stage3_emails

    # Patch settings to have Snov.io credentials
    mock_settings = MagicMock()
    mock_settings.snov_client_id = "test_id"
    mock_settings.snov_client_secret = "test_secret"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    token_resp = MagicMock()
    token_resp.ok = True
    token_resp.json.return_value = {"access_token": "tok123"}

    domain_resp = MagicMock()
    domain_resp.ok = True
    domain_resp.json.return_value = {
        "emails": [
            {"email": "webmaster@clinic.com"},
            {"email": "info@clinic.com"},
            {"email": "drsmith@clinic.com"},
        ]
    }

    with patch("app.scraper.stage3_emails.requests.post", side_effect=[token_resp, domain_resp]):
        result = snov_lookup("clinic.com")

    assert result["email"] == "drsmith@clinic.com"
    assert result["method"] == "snov_domain"
    assert "info@clinic.com" in result["all_emails"]


def test_snov_lookup_returns_not_found_when_empty(monkeypatch):
    from app.scraper.stage3_emails import snov_lookup
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.snov_client_id = "test_id"
    mock_settings.snov_client_secret = "test_secret"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    token_resp = MagicMock()
    token_resp.ok = True
    token_resp.json.return_value = {"access_token": "tok123"}

    domain_resp = MagicMock()
    domain_resp.ok = True
    domain_resp.json.return_value = {"emails": []}

    with patch("app.scraper.stage3_emails.requests.post", side_effect=[token_resp, domain_resp]):
        result = snov_lookup("clinic.com")

    assert result["email"] is None
    assert result["method"] == "snov_not_found"


def test_snov_lookup_returns_error_when_token_fails(monkeypatch):
    from app.scraper.stage3_emails import snov_lookup
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.snov_client_id = "test_id"
    mock_settings.snov_client_secret = "test_secret"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    token_resp = MagicMock()
    token_resp.ok = False

    with patch("app.scraper.stage3_emails.requests.post", return_value=token_resp):
        result = snov_lookup("clinic.com")

    assert result["email"] is None
    assert result["method"] == "snov_error"
