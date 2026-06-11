"""
STAGE 2 — Groq categorizer (llama-3.3-70b-versatile).

Reads clinics_raw.json and, per clinic, asks the LLM to read the reviews and
return the qualification fields from the Daryaft Labs plan (section 7.2):

    qualification_score   int 0-10  (6+ => recommend_outreach)
    primary_pain_point    missed_calls | no_after_hours | no_online_booking | slow_response | none
    complaint_quote       exact short quote from a 1-3 star review, or null
    personalization_hook  one specific sentence about this clinic
    recommend_outreach    bool

Setup:  pip install openai python-dotenv   &&   fill in .env
Run:    python categorize.py
"""

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

INPUT = "clinics_raw.json"
OUTPUT = "clinics_qualified.json"
MODEL = "llama-3.3-70b-versatile"
MAX_REVIEWS_FOR_LLM = 60
MAX_REVIEW_CHARS = 600

PAIN_ENUM = ["missed_calls", "no_after_hours", "no_online_booking",
             "slow_response", "none"]

SYSTEM = (
    "You are a lead-qualification analyst for an AI voice-receptionist agency "
    "that sells to small US dental practices. You read a clinic's patient "
    "reviews and decide whether the clinic shows phone-handling, booking, or "
    "responsiveness pain that an AI receptionist would solve "
    "(missed/unanswered calls, no after-hours coverage, no online booking, "
    "slow callbacks, long holds, scheduling mix-ups). "
    "You output STRICT JSON only."
)

INSTRUCTIONS = f"""For the clinic below, analyze ONLY the provided reviews and return a single JSON object:

{{
  "qualification_score": <int 0-10, how strong the phone/booking/responsiveness pain is>,
  "primary_pain_point": <one of {PAIN_ENUM}>,
  "complaint_quote": <an EXACT substring copied from one 1-3 star review that shows the pain, max ~25 words, or null>,
  "complaint_review_index": <the index of the review the quote came from, or null>,
  "personalization_hook": <one specific sentence we can open a cold email with, grounded in the reviews>,
  "recommend_outreach": <true if qualification_score >= 6 else false>
}}

Rules:
- complaint_quote MUST be copied verbatim from one of the reviews (no paraphrasing). If no review shows relevant pain, set it and complaint_review_index to null and primary_pain_point to "none".
- Do not invent problems. A clinic of glowing reviews scores low.
- Return JSON only, no markdown, no commentary."""


def _slim_reviews(clinic):
    revs = clinic.get("reviews", [])[:MAX_REVIEWS_FOR_LLM]
    return [
        {"index": i,
         "rating": r.get("rating"),
         "date": r.get("date_approx") or r.get("date_text"),
         "text": (r.get("text") or "")[:MAX_REVIEW_CHARS]}
        for i, r in enumerate(revs)
    ]


def categorize(client, clinic):
    slim = _slim_reviews(clinic)
    if not slim:
        return {"qualification_score": 0, "primary_pain_point": "none",
                "complaint_quote": None, "complaint_review_index": None,
                "personalization_hook": "", "recommend_outreach": False}

    payload = {"clinic_name": clinic.get("name"), "reviews": slim}
    user_msg = INSTRUCTIONS + "\n\nCLINIC DATA:\n" + json.dumps(payload, ensure_ascii=False)

    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": user_msg}],
            )
            return json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError:
            user_msg += "\n\nYour last reply was not valid JSON. Return ONLY the JSON object."
        except Exception as e:
            if attempt < 3 and ("429" in str(e) or "rate" in str(e).lower()):
                wait = 20 * (attempt + 1)
                print(f"      [rate-limit] retrying in {wait}s …")
                time.sleep(wait)
            else:
                raise

    return {"qualification_score": 0, "primary_pain_point": "none",
            "complaint_quote": None, "complaint_review_index": None,
            "personalization_hook": "", "recommend_outreach": False,
            "error": "failed_after_retries"}


def main():
    with open(INPUT, encoding="utf-8") as fh:
        data = json.load(fh)
    clinics = data.get("clinics", [])
    client = OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
    )

    out = []
    for i, c in enumerate(clinics, 1):
        print(f"[2] ({i}/{len(clinics)}) categorizing {c.get('name')}")
        q = categorize(client, c)

        idx = q.get("complaint_review_index")
        ref = None
        if isinstance(idx, int) and 0 <= idx < len(c.get("reviews", [])):
            src = c["reviews"][idx]
            ref = {"author": src.get("author"), "date": src.get("date_approx"),
                   "review_id": src.get("review_id")}

        out.append({
            "clinic": {
                "name": c.get("name"), "address": c.get("address"),
                "phone": c.get("phone"), "website": c.get("website"),
                "google_maps_url": c.get("google_maps_url"),
                "rating": c.get("rating"), "review_count": c.get("review_count"),
                "reviews_analyzed": len(c.get("reviews", [])),
            },
            "qualification": {
                "qualification_score": q.get("qualification_score"),
                "primary_pain_point": q.get("primary_pain_point"),
                "complaint_quote": q.get("complaint_quote"),
                "complaint_review_url": c.get("google_maps_url"),
                "complaint_review_ref": ref,
                "personalization_hook": q.get("personalization_hook"),
                "recommend_outreach": q.get("recommend_outreach"),
            },
        })

    result = {
        "run_meta": {
            "stage": "categorize", "model": MODEL,
            "clinics": len(out),
            "recommended": sum(1 for x in out
                               if x["qualification"]["recommend_outreach"]),
        },
        "clinics": out,
    }
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"[2] wrote {OUTPUT} — "
          f"{result['run_meta']['recommended']}/{len(out)} recommended for outreach")


if __name__ == "__main__":
    main()
