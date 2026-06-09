"""
Run the full pipeline end to end:

    Stage 1  scrape_clinics.py   (Camoufox)      -> clinics_raw.json
    Stage 2  categorize.py       (Gemini)        -> clinics_qualified.json
    Stage 3  find_emails.py      (Browser Use)   -> leads_final.json

You can also run each stage on its own (re-run Stage 2/3 without re-scraping).

Run:  python run_all.py
"""

import scrape_clinics
import categorize
import find_emails

if __name__ == "__main__":
    print("=== STAGE 1: scrape (Camoufox) ===")
    scrape_clinics.main()
    print("\n=== STAGE 2: categorize (Gemini) ===")
    categorize.main()
    print("\n=== STAGE 3: find emails (Browser Use) ===")
    find_emails.main()
    print("\nDone. Final leads in leads_final.json")
