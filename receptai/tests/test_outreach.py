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


# ---------------------------------------------------------------------------
# Integration test — campaign creation and email listing
# ---------------------------------------------------------------------------

async def test_create_campaign_and_get_emails():
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    # ── In-memory stores ────────────────────────────────────────────────────
    profiles = {}
    niches_store = {"dental": {"id": "niche-dental-id", "slug": "dental"}}
    campaigns_store = {}
    outreach_emails_store = {}

    def make_mock_db():
        mock_db = MagicMock()

        def table(name):
            t = MagicMock()

            def select(*args):
                s = MagicMock()

                def eq(col, val):
                    e = MagicMock()

                    def execute():
                        r = MagicMock()
                        if name == "profiles":
                            r.data = [p for p in profiles.values() if p.get(col) == val]
                        elif name == "niches":
                            r.data = [v for v in niches_store.values() if v.get(col) == val]
                        elif name == "campaigns":
                            r.data = [c for c in campaigns_store.values() if c.get(col) == val]
                        elif name == "outreach_emails":
                            r.data = [
                                em for em in outreach_emails_store.values()
                                if em.get(col) == val
                            ]
                        else:
                            r.data = []
                        return r

                    e.execute = execute
                    # support chained .eq().eq() — second call is a no-op
                    e.eq = lambda c2, v2: e
                    return e

                s.eq = eq
                return s

            def insert(row):
                i = MagicMock()

                def execute():
                    if name == "profiles":
                        profiles[row["id"]] = row
                    elif name == "campaigns":
                        campaigns_store[row["id"]] = row
                    elif name == "outreach_emails":
                        outreach_emails_store[row["id"]] = row
                    r = MagicMock()
                    r.data = [row]
                    return r

                i.execute = execute
                return i

            t.select = select
            t.insert = insert
            t.update = MagicMock(
                return_value=MagicMock(
                    eq=MagicMock(
                        return_value=MagicMock(execute=MagicMock())
                    )
                )
            )
            return t

        mock_db.table = table
        return mock_db

    db_instance = make_mock_db()

    with patch("app.auth.service.get_db", return_value=db_instance), \
         patch("app.outreach.repository.get_db", return_value=db_instance), \
         patch("app.outreach.router.get_db", return_value=db_instance):

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            # Register + login
            await c.post(
                "/auth/register",
                json={"email": "camp@test.com", "password": "Pass123!"},
            )
            r = await c.post(
                "/auth/token",
                data={"username": "camp@test.com", "password": "Pass123!"},
            )
            token = r.json()["access_token"]
            c.headers["Authorization"] = f"Bearer {token}"

            # Create campaign
            r = await c.post(
                "/outreach/campaigns",
                json={"name": "June Dental Blast", "niche": "dental"},
            )
            assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
            campaign_id = r.json()["id"]

            # List emails (should be empty)
            r = await c.get(f"/outreach/campaigns/{campaign_id}/emails")
            assert r.status_code == 200
            assert r.json() == []
