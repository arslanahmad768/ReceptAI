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
