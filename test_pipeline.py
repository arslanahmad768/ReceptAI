"""
Full email discovery pipeline tester.

Tests find_email_for_clinic() across a list of real dental clinics and
reports which step found the email, the contact's role, and overall
retrieval percentage.

Run:  python test_pipeline.py
"""

import asyncio
import sys
import time
from pathlib import Path

# ── Make sure app/ is importable ──────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Load .env before importing app modules
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.scraper.stage3_emails import find_email_for_clinic  # noqa: E402

# ── Test clinics ──────────────────────────────────────────────────────────────
# Format: (clinic_name, website)
CLINICS = [
    ("Bright Smiles Dental",    "https://brightsmilesdental.com"),
    ("Aspen Dental",            "https://aspendental.com"),
    ("Comfort Dental",          "https://comfortdental.com"),
    ("Riverside Dental Care",   "https://riversidedentalcare.com"),
    ("Smile Designers Dental",  "https://smiledesignersdental.com"),
    ("Family First Dental",     "https://familyfirstdental.com"),
    ("Castle Dental",           "https://castledental.com"),
    ("Pacific Dental Services", "https://pacificdental.com"),
    ("Gentle Dental",           "https://gentledental.com"),
    ("Kool Smiles",             "https://koolsmiles.com"),
]

# ── Step label map ─────────────────────────────────────────────────────────────
STEP_LABELS = {
    "html_direct":           "Step 0   — HTML scrape",
    "smtp_verified":         "Step 1   — SMTP pattern (NPI owner patterns included)",
    "smtp_unverified":       "Step 1   — SMTP (unverified)",
    "hunter_verified":       "Step 1   — SMTP catch-all → Hunter verify",
    "snov_domain":           "Step 2   — Snov.io",
    "serp":                  "Step 3   — SERP / Google",
    "browser_scraped":       "Step 4   — Browser agent",
    "hunter_domain":         "Step 5   — Hunter.io",
    "hunter_finder":         "Step 5   — Hunter.io finder",
    "not_found":             "—        not found",
    "contact_form_only":     "—        contact form only",
    "error":                 "—        error",
}

# ── Role priority label (shown next to email) ──────────────────────────────────
# Roles that are decision-makers get a star marker so they stand out
DECISION_MAKER_ROLES = {
    "CEO", "Owner", "Founder / Owner", "Co-Founder",
    "President", "Director", "Manager", "General Manager",
    "Principal", "Partner", "VP", "Doctor / Dentist",
}


# ── Runner ────────────────────────────────────────────────────────────────────
async def run_all():
    results = []

    print("\n" + "="*76)
    print("  RECEPTAI — Full Email Discovery Pipeline Test")
    print("="*76)

    for clinic_name, website in CLINICS:
        print(f"\n{'─'*76}")
        print(f"  Clinic  : {clinic_name}")
        print(f"  Website : {website}")
        print(f"{'─'*76}")

        t0 = time.time()
        try:
            result = await find_email_for_clinic(website, clinic_name)
        except Exception as exc:
            result = {
                "email": None, "method": "error",
                "all_emails": [], "doctors_found": [],
                "contact_role": None,
            }
            print(f"  ERROR: {exc}")

        elapsed = time.time() - t0

        email   = result.get("email")
        method  = result.get("method", "not_found")
        all_em  = result.get("all_emails", [])
        doctors = result.get("doctors_found", [])
        role    = result.get("contact_role")
        step    = STEP_LABELS.get(method, method)

        found = "YES" if email else "NO"
        marker = " ★" if role in DECISION_MAKER_ROLES else ""

        print(f"  Result  : {found}")
        if email:
            print(f"  Email   : {email}")
            print(f"  Role    : {role or 'Unknown (generic inbox)'}{marker}")
        if all_em and len(all_em) > 1:
            others = [e for e in all_em if e != email]
            print(f"  Other   : {others}")
        if doctors:
            print(f"  Doctors : {', '.join(doctors)}")
        print(f"  Step    : {step}")
        print(f"  Time    : {elapsed:.1f}s")

        results.append({
            "clinic":  clinic_name,
            "email":   email,
            "role":    role,
            "method":  method,
            "step":    step,
            "elapsed": elapsed,
            "found":   bool(email),
            "is_decision_maker": role in DECISION_MAKER_ROLES if role else False,
        })

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n\n" + "="*76)
    print("  SUMMARY")
    print("="*76)
    print(f"  {'Clinic':<26} {'Email':<32} {'Role':<22} Step")
    print(f"  {'─'*26} {'─'*32} {'─'*22} {'─'*18}")

    found_count = 0
    dm_count    = 0
    step_counts: dict[str, int] = {}

    for r in results:
        email_str = (r["email"] or "—")[:31]
        role_str  = (r["role"] or "—")[:21]
        marker    = " ★" if r["is_decision_maker"] else ""
        print(f"  {r['clinic']:<26} {email_str:<32} {role_str+marker:<24} {r['method']}")
        if r["found"]:
            found_count += 1
            step_counts[r["method"]] = step_counts.get(r["method"], 0) + 1
        if r["is_decision_maker"]:
            dm_count += 1

    total = len(results)
    pct    = (found_count / total * 100) if total else 0
    dm_pct = (dm_count / found_count * 100) if found_count else 0

    print(f"\n  Total clinics tested      : {total}")
    print(f"  Emails found              : {found_count}  ({pct:.0f}%)")
    print(f"  NOT found                 : {total - found_count}")
    print(f"  Decision-maker emails ★   : {dm_count}  ({dm_pct:.0f}% of found)")

    if step_counts:
        print(f"\n  Breakdown by step:")
        for step_key, count in sorted(step_counts.items(), key=lambda x: -x[1]):
            label = STEP_LABELS.get(step_key, step_key)
            print(f"    {count:2d}x  {label}")

    print(f"\n  ★ = decision-maker role (Doctor / Manager / Director / Owner / CEO ...)")
    print(f"  Total time: {sum(r['elapsed'] for r in results):.1f}s")
    print("="*76 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all())
