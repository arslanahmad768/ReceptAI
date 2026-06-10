# tests/test_outreach.py
import pytest
from unittest.mock import patch, MagicMock
from app.outreach.email_generator import generate_cold_email, _render_template_vars
from app.niches.dental import DentalConfig

def test_template_vars_are_replaced():
    text = "Hi {{first_name}}, check out {{demo_url}}"
    result = _render_template_vars(text, {"first_name": "Sarah", "demo_url": "https://demo.receptai.com"})
    assert "{{" not in result
    assert "Sarah" in result

    # None value should not leave a {{placeholder}} behind
    text2 = "Call us at {{phone}}"
    result2 = _render_template_vars(text2, {"phone": None})
    assert "{{" not in result2

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
    captured = []
    def capture(prompt, system_prompt):
        captured.append(prompt)
        return '{"subject": "Quick question for Bright Smile Dental", "body": "Hi there, we noticed..."}'
    with patch("app.outreach.email_generator._call_llm", side_effect=capture):
        result = generate_cold_email(lead, niche_cfg)
    assert "subject" in result and "body" in result
    assert len(result["body"]) > 10
    assert "Bright Smile Dental" in captured[0]

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


from app.outreach.sender import send_email, SendResult

def test_send_email_returns_send_result():
    mock_email = MagicMock()
    mock_email.id = "re_test_123"
    with patch("app.outreach.sender.resend.Emails.send", return_value=mock_email):
        result = send_email(
            to="test@clinic.com",
            subject="Test subject",
            body="Test body\n\nWith lines",
        )
    assert isinstance(result, SendResult)
    assert result.message_id == "re_test_123"
    assert result.success is True
