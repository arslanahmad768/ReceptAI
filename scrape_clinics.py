"""
STAGE 1 — Camoufox scraper (COLLECTION ONLY).

Discovers the first N Tampa dental clinics, then for each: reads Overview-tab
details (incl. website), sorts reviews Newest, scrolls back to the cutoff, and
dumps every review (author, rating, date, text) to clinics_raw.json.

No LLM. No keyword matching. Categorization happens in Stage 2.

Setup:  pip install "camoufox[geoip]"   &&   python -m camoufox fetch
Run:    python scrape_clinics.py
"""

import json
import time
from datetime import datetime, timedelta, timezone

from camoufox.sync_api import Camoufox

import maps_dom

SEARCH_TERM = "dental clinics in Tampa FL"
LIMIT = 10
HEADLESS = True

# Review depth. Default: last 12 months. For "back to 2022" set CUTOFF_DATE.
CUTOFF_MONTHS = 12
CUTOFF_DATE = None            # e.g. "2022-01-01" to override the months window

OUTPUT = "clinics_raw.json"


def _cutoff():
    if CUTOFF_DATE:
        return datetime.fromisoformat(CUTOFF_DATE).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timedelta(days=30 * CUTOFF_MONTHS)


def main():
    t0 = time.time()
    cutoff = _cutoff()
    print(f"[1] Review cutoff: {cutoff.date().isoformat()}")

    clinics = []
    with Camoufox(headless=HEADLESS, humanize=True) as browser:
        page = browser.new_page()

        print(f"[1] Discovering up to {LIMIT} clinics...")
        found = maps_dom.discover_clinics(page, SEARCH_TERM, LIMIT)
        print(f"[1] Found {len(found)}.")

        for i, f in enumerate(found, 1):
            print(f"[1] ({i}/{len(found)}) {f['name']}")
            try:
                c = maps_dom.scrape_place_reviews(page, f["google_maps_url"], cutoff)
                c["google_maps_url"] = f["google_maps_url"]
                c.setdefault("name", f["name"])
                clinics.append(c)
                print(f"      {len(c.get('reviews', []))} reviews in window "
                      f"(newest-sorted: {c.get('sorted_newest')})")
            except Exception as e:
                print(f"      ERROR: {e}")
            maps_dom.pause(3, 6)

    out = {
        "run_meta": {
            "stage": "scrape",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cutoff_date": cutoff.date().isoformat(),
            "clinics": len(clinics),
            "total_reviews": sum(len(c.get("reviews", [])) for c in clinics),
            "scrape_seconds": round(time.time() - t0, 1),
        },
        "clinics": clinics,
    }
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"[1] wrote {OUTPUT} "
          f"({out['run_meta']['clinics']} clinics, "
          f"{out['run_meta']['total_reviews']} reviews)")


if __name__ == "__main__":
    main()
