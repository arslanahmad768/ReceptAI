"""
AI cold email generator.

Uses an expert persona (NicheConfig.email_generator_persona) as the system prompt
so the LLM writes from the perspective of someone who knows the industry inside-out.
Demo links are injected from settings so every email includes the live demo CTA.
"""
import json
import re
from openai import OpenAI
from app.config import settings
from app.niches.base import NicheConfig

# ── Prompt template ───────────────────────────────────────────────────────────

_USER_PROMPT = """Write a cold email for the dental practice below.

Context:
- Product: {value_proposition}
- Niche context: {niche_context}
- Demo phone: {demo_phone}
- Demo URL: {demo_url}
- Booking URL: {booking_url}

Clinic:
- Name: {clinic_name}
- Personalization hook: {personalization_hook}
- Pain point category: {pain_point}
- Patient complaint quote (verbatim, use only if natural): "{complaint_quote}"

Instructions:
- Subject: max 8 words, curiosity-driving, no spam trigger words (free, guaranteed, act now)
- Body: 3-5 short paragraphs, conversational, 80 words MAX
- Must include the demo phone OR demo URL in the body (or both)
- End with a single low-friction CTA — make it feel easy, not salesy
- If the complaint quote is strong, open the body with it in quotes then address it

Return ONLY valid JSON, no markdown:
{{"subject": "...", "body": "..."}}"""


# ── Template variable renderer ────────────────────────────────────────────────

def _render_template_vars(text: str, variables: dict) -> str:
    """Replace {{key}} placeholders with values from the variables dict."""
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value or ""))
    return text


# ── LLM call (isolated so tests can mock it) ─────────────────────────────────

def _call_llm(prompt: str, system_prompt: str) -> str:
    client = OpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1")
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0.75,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


# ── Public API ────────────────────────────────────────────────────────────────

def generate_cold_email(lead: dict, niche_cfg: NicheConfig) -> dict:
    """
    Generate a personalised cold email.

    lead shape:
      {"clinic": {"name": str}, "qualification": {"personalization_hook": str,
       "complaint_quote": str|None, "primary_pain_point": str}}

    Returns {"subject": str, "body": str}
    """
    clinic = lead.get("clinic", {})
    qual = lead.get("qualification", {})

    prompt = _USER_PROMPT.format(
        value_proposition=niche_cfg.value_proposition,
        niche_context=niche_cfg.cold_email_context,
        demo_phone=settings.demo_phone or "[demo phone]",
        demo_url=settings.demo_url or "[demo URL]",
        booking_url=settings.booking_url or "[booking URL]",
        clinic_name=clinic.get("name", ""),
        personalization_hook=qual.get("personalization_hook", ""),
        pain_point=qual.get("primary_pain_point", ""),
        complaint_quote=qual.get("complaint_quote") or "N/A",
    )

    raw = _call_llm(prompt, niche_cfg.email_generator_persona)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        result = json.loads(m.group(0)) if m else {"subject": "", "body": raw}

    # Replace any remaining {{placeholders}} the LLM left in the output
    template_vars = {
        "clinic_name": clinic.get("name", ""),
        "demo_phone": settings.demo_phone,
        "demo_url": settings.demo_url,
        "booking_url": settings.booking_url,
    }
    result["subject"] = _render_template_vars(result.get("subject", ""), template_vars)
    result["body"] = _render_template_vars(result.get("body", ""), template_vars)

    return result
