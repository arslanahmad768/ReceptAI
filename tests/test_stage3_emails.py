import pytest
from unittest.mock import patch, MagicMock


# ── Quality scoring ───────────────────────────────────────────────────────────

def test_email_priority_score_dr_prefix():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("drsmith@clinic.com") == 85

def test_email_priority_score_dr_dot_prefix():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("dr.jones@clinic.com") == 85

def test_email_priority_score_info():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("info@clinic.com") == 75

def test_email_priority_score_office():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("office@clinic.com") == 75

def test_email_priority_score_frontdesk():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("frontdesk@clinic.com") == 45

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


# ── SERP ──────────────────────────────────────────────────────────────────────

def test_serp_email_search_skipped_when_no_key(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.serpapi_key = None
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    result = serp_email_search("Sunny Dental", "sunnydental.com")
    assert result["email"] is None
    assert result["method"] == "serp_skipped"


def test_serp_email_search_finds_email_in_snippet(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    serp_resp.json.return_value = {
        "organic_results": [
            {
                "title": "Sunny Dental Contact",
                "snippet": "Contact us at info@sunnydental.com for appointments.",
            }
        ]
    }

    with patch("app.scraper.stage3_emails.requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] == "info@sunnydental.com"
    assert result["method"] == "serp"


def test_serp_email_search_picks_best_of_multiple(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    serp_resp.json.return_value = {
        "organic_results": [
            {
                "snippet": (
                    "webmaster@sunnydental.com or "
                    "drjones@sunnydental.com or "
                    "info@sunnydental.com"
                )
            }
        ]
    }

    with patch("app.scraper.stage3_emails.requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] == "drjones@sunnydental.com"


def test_serp_email_search_returns_not_found_when_no_emails(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    serp_resp.json.return_value = {"organic_results": [{"snippet": "No email here."}]}

    with patch("app.scraper.stage3_emails.requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] is None
    assert result["method"] == "serp_not_found"


# ── NPI helpers ───────────────────────────────────────────────────────────────

def test_extract_state_zip_full_address():
    from app.scraper.stage3_emails import _extract_state_zip
    state, zip_ = _extract_state_zip("123 Main St, Austin, TX 78701, USA")
    assert state == "TX"
    assert zip_ == "78701"

def test_extract_state_zip_no_zip():
    from app.scraper.stage3_emails import _extract_state_zip
    state, zip_ = _extract_state_zip("456 Oak Ave, Houston, TX, USA")
    assert state == "TX"
    assert zip_ is None

def test_extract_state_zip_empty():
    from app.scraper.stage3_emails import _extract_state_zip
    assert _extract_state_zip("") == (None, None)
    assert _extract_state_zip(None) == (None, None)

def test_owner_name_patterns():
    from app.scraper.stage3_emails import _owner_name_patterns
    patterns = _owner_name_patterns("John", "Smith", "clinic.com")
    assert "john.smith@clinic.com" in patterns
    assert "jsmith@clinic.com" in patterns
    assert "johnsmith@clinic.com" in patterns
    # No dr. prefix for owner patterns
    assert not any("dr" in p.split("@")[0] for p in patterns)

def test_npi_owner_lookup_skipped_without_state(monkeypatch):
    from app.scraper.stage3_emails import npi_owner_lookup
    result = npi_owner_lookup("Bright Smiles Dental", None, None)
    assert result["first_name"] is None
    assert result["last_name"] is None

def test_npi_owner_lookup_returns_owner(monkeypatch):
    from app.scraper.stage3_emails import npi_owner_lookup
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "results": [{
            "basic": {
                "authorized_official_first_name": "JANE",
                "authorized_official_last_name": "DOE",
                "authorized_official_title_or_position": "OWNER",
            }
        }]
    }
    with patch("app.scraper.stage3_emails.requests.get", return_value=mock_resp):
        result = npi_owner_lookup("Bright Smiles Dental", "TX", "78701")
    assert result["first_name"] == "JANE"
    assert result["last_name"] == "DOE"
    assert result["title"] == "OWNER"

def test_npi_owner_lookup_returns_empty_on_no_results(monkeypatch):
    from app.scraper.stage3_emails import npi_owner_lookup
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"results": []}
    with patch("app.scraper.stage3_emails.requests.get", return_value=mock_resp):
        result = npi_owner_lookup("Unknown Clinic", "TX", "78701")
    assert result["first_name"] is None


def test_serp_email_search_does_not_misextract_subdomain_tld(monkeypatch):
    """@clinic.com must not be extracted from @clinic.com.au (false positive)."""
    from app.scraper.stage3_emails import serp_email_search
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    serp_resp.json.return_value = {
        "organic_results": [{"snippet": "Email us at info@sunnydental.com.au for bookings."}]
    }

    with patch("app.scraper.stage3_emails.requests.get", return_value=serp_resp):
        # searching for sunnydental.com — must NOT match sunnydental.com.au
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] is None


def test_serp_email_search_only_returns_domain_emails(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app.scraper import stage3_emails

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(stage3_emails, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    # snippet contains an email from a DIFFERENT domain — must not be returned
    serp_resp.json.return_value = {
        "organic_results": [{"snippet": "Contact support@otherdomain.com for help."}]
    }

    with patch("app.scraper.stage3_emails.requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] is None

