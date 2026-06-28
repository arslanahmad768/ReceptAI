"""Stage 2 — Groq qualifier. Pure function; no file I/O."""
import json
import time
from openai import OpenAI
from app.config import get_settings

MODEL = "llama-3.3-70b-versatile"
MAX_REVIEWS = 60
MAX_CHARS = 600
PAIN_ENUM = ["missed_calls", "no_after_hours", "no_online_booking", "slow_response", "none"]

_INSTRUCTIONS = f"""Analyze the clinic's reviews and return a single JSON object:

{{
  "qualification_score": <int 0-10, strength of phone/booking pain>,
  "primary_pain_point": <one of {PAIN_ENUM}>,
  "complaint_quote": <EXACT substring from a 1-3 star review showing the pain, max 25 words, or null>,
  "complaint_review_index": <index of that review, or null>,
  "personalization_hook": <one specific sentence to open a cold email, grounded in reviews>,
  "recommend_outreach": <true if qualification_score >= 6>
}}

Rules:
- complaint_quote MUST be verbatim. If no relevant pain exists, set to null and primary_pain_point to "none".
- Do not invent problems. Glowing reviews = low score.
- Return JSON only, no markdown, no commentary."""


def _slim_reviews(clinic: dict) -> list[dict]:
    return [
        {"index": i,
         "rating": r.get("rating"),
         "date": r.get("date_approx") or r.get("date_text"),
         "text": (r.get("text") or "")[:MAX_CHARS]}
        for i, r in enumerate(clinic.get("reviews", [])[:MAX_REVIEWS])
    ]


def categorize_clinic(clinic: dict, system_prompt: str) -> dict:
    slim = _slim_reviews(clinic)
    if not slim:
        return {"qualification_score": 0, "primary_pain_point": "none",
                "complaint_quote": None, "complaint_review_index": None,
                "personalization_hook": "", "recommend_outreach": False}

    s = get_settings()
    client = OpenAI(api_key=s.groq_api_key, base_url="https://api.groq.com/openai/v1")
    payload = {"clinic_name": clinic.get("name"), "reviews": slim}
    user_msg = _INSTRUCTIONS + "\n\nCLINIC DATA:\n" + json.dumps(payload, ensure_ascii=False)

    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=MODEL, temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_msg}],
            )
            return json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError:
            user_msg += "\n\nYour last reply was not valid JSON. Return ONLY the JSON object."
        except Exception as e:
            if attempt < 3 and ("429" in str(e) or "rate" in str(e).lower()):
                time.sleep(20 * (attempt + 1))
            else:
                raise

    return {"qualification_score": 0, "primary_pain_point": "none",
            "complaint_quote": None, "complaint_review_index": None,
            "personalization_hook": "", "recommend_outreach": False,
            "error": "failed_after_retries"}
