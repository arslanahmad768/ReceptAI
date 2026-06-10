# tests/test_outreach.py
import pytest
from unittest.mock import patch
from app.outreach.email_generator import generate_cold_email, _render_template_vars
from app.niches.dental import DentalConfig

def test_template_vars_are_replaced():
    text = "Hi {{first_name}}, check out {{demo_url}}"
    result = _render_template_vars(text, {"first_name": "Sarah", "demo_url": "https://demo.receptai.com"})
    assert "{{" not in result
    assert "Sarah" in result

def test_generate_cold_email_returns_subject_and_body():
    lead = {
        "clinic": {"name": "Bright Smile Dental"},
        "qualification": {
            "personalization_hook": "Several reviews mention calls going unanswered.",
            "complaint_quote": "Called 3 times and nobody ever picked up",
            "primary_pain_point": "missed_calls",
        }
    }
    niche_cfg = DentalConfig(niche_id="x", location="Tampa FL")
    mock_resp = '{"subject": "Quick question for Bright Smile Dental", "body": "Hi there, we noticed..."}'
    with patch("app.outreach.email_generator._call_llm", return_value=mock_resp):
        result = generate_cold_email(lead, niche_cfg)
    assert "subject" in result and "body" in result
    assert len(result["body"]) > 10

def test_generate_uses_complaint_quote_in_prompt():
    lead = {
        "clinic": {"name": "Test Dental"},
        "qualification": {
            "personalization_hook": "Patients complain about hold times.",
            "complaint_quote": "Was on hold for 40 minutes",
            "primary_pain_point": "slow_response",
        }
    }
    niche_cfg = DentalConfig(niche_id="x", location="Tampa FL")
    captured_prompts = []
    def capture(prompt, system_prompt):
        captured_prompts.append(prompt)
        return '{"subject": "s", "body": "b"}'
    with patch("app.outreach.email_generator._call_llm", side_effect=capture):
        generate_cold_email(lead, niche_cfg)
    assert "Was on hold for 40 minutes" in captured_prompts[0]
