"""
One-off sample run of Stage 1 (Google Maps scrape) — dumps results to JSON.

Run:  uv run python scripts/sample_scrape.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper.stage1_scrape import run_stage1  # noqa: E402

SEARCH_TERM = "dentist"
LOCATION = "Austin, TX"
LIMIT = 5

if __name__ == "__main__":
    clinics = run_stage1(SEARCH_TERM, LOCATION, limit=LIMIT, cutoff_months=12)

    out_path = ROOT / "sample_scrape_output.json"
    out_path.write_text(json.dumps(clinics, indent=2, default=str))

    print(f"Scraped {len(clinics)} clinics → {out_path}")
