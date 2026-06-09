# Daryaft Labs — dental lead scraper (Modules 1 + 2a)

Three-stage pipeline. The scraper only **collects**; an LLM does the
**categorization**; Browser Use does the **email finding**. This matches the
plan: scraping is ammunition for the qualification engine, not the product.

```
Stage 1  scrape_clinics.py   Camoufox     -> clinics_raw.json        (raw reviews, no judgment)
Stage 2  categorize.py       OpenAI       -> clinics_qualified.json  (score + complaint quote + hook)
Stage 3  find_emails.py      Browser Use  -> leads_final.json        (contact email per clinic)
```

## What each stage does
- **Stage 1 (Camoufox, no LLM):** discover the first 10 Tampa dental clinics →
  for each: read the **Overview** tab (name, address, phone, **website**), open
  **Reviews**, sort **Newest**, scroll back to the cutoff, and dump **every**
  review (author, rating, date, text). No keywords, no categorization.
- **Stage 2 (OpenAI):** reads the raw reviews and returns the plan's §7.2 fields —
  `qualification_score` 0-10, `primary_pain_point`
  (missed_calls / no_after_hours / no_online_booking / slow_response / none),
  an exact `complaint_quote`, a `personalization_hook`, and `recommend_outreach`.
- **Stage 3 (Browser Use):** for each recommended clinic with a website, an agent
  visits the site and navigates Contact / About / Team pages to find an email —
  the part that beats regex on chain sites that hide contact behind forms.

## Setup
```bash
python3 -m venv venv && source venv/bin/activate
pip install "camoufox[geoip]" "browser-use" openai
python -m camoufox fetch          # one-time stealth browser (~300MB)
export OPENAI_API_KEY="sk-..."     # used by Stage 2 and Stage 3
```

## Run
```bash
python run_all.py                  # all three stages in order
# or individually (re-run 2/3 without re-scraping):
python scrape_clinics.py
python categorize.py
python find_emails.py
```

## Review depth (one setting)
In `scrape_clinics.py`:
- `CUTOFF_MONTHS = 12`  → default, last 12 months (recommended).
- `CUTOFF_DATE = "2022-01-01"` → overrides the months window to go back to 2022.

Going back to 2022 means a lot more scrolling per clinic on a single IP, which
raises block risk and runtime for little extra quote value. 12 months almost
always contains the recent phone-pain a cold email would quote.

## Notes
- Run Stage 1 with `HEADLESS = False` the first time so you can confirm the
  Reviews tab opens and the **Sort → Newest** step works. If sort fails, the
  scraper still runs but the cutoff is less precise (it logs `sorted_newest`).
- `complaint_review_url` points at the clinic's Maps profile — Google doesn't
  expose reliable per-review deep links, so Stage 2 also stores
  `complaint_review_ref` (author + date + review_id) to locate the exact review.
- Stage 3 checkpoints after each clinic; re-run to resume if it stalls.
- This is the standalone Python/Camoufox tool. Porting to the doc's
  Node/Puppeteer + Supabase monorepo (Module 1) is a later step — the JSON
  shapes here already line up with the `clinics` and `reviews` tables in §12.
- Scraping Maps is against Google's ToS; keep volume low and rate-limited
  (the plan's §6.4 discipline).
