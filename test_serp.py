"""
Standalone SERP API email extraction tester.
Run: python test_serp.py
"""

import json
import re
import requests

# ── Config ────────────────────────────────────────────────────────────────────
SERPAPI_KEY = "901afcad36b6940475db7e6a129d51a5ec3e7848f1c3177a47a2cb4f0df16467"
SERPAPI_URL = "https://serpapi.com/search.json"

# ── Change these to test different clinics ────────────────────────────────────
CLINIC_NAME = "Bright Smiles Dental"
CLINIC_DOMAIN = "brightsmilesdental.com"


# ── Email scoring (same logic as stage3_emails.py) ────────────────────────────
_EMAIL_SCORES = {
    "info": 90, "office": 90, "frontdesk": 85, "front": 80,
    "contact": 80, "hello": 75, "appointments": 70, "scheduling": 70,
    "booking": 70, "reception": 70, "support": 50,
    "admin": 40, "billing": 35, "accounts": 30,
    "webmaster": 0, "noreply": 0, "donotreply": 0,
    "postmaster": 0, "hostmaster": 0, "abuse": 0,
}

def email_score(email: str) -> int:
    prefix = email.split("@")[0].lower()
    if re.match(r"^dr[.\-]?[a-z]", prefix):
        return 100
    clean = re.sub(r"[.\-_]", "", prefix)
    for keyword, score in _EMAIL_SCORES.items():
        if re.sub(r"[.\-_]", "", keyword) == clean:
            return score
    return 70  # unknown personal name — likely valid

def best_email(emails: list[str]) -> str | None:
    if not emails:
        return None
    return max(emails, key=email_score)


# ── Main extraction logic (mirrors stage3_emails.py exactly) ─────────────────
def serp_email_search(clinic_name: str, domain: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Clinic : {clinic_name}")
    print(f"  Domain : {domain}")
    print(f"{'='*60}\n")

    queries = [
        f'"@{domain}"',
        f'"{clinic_name}" email',
        f'site:{domain} contact email',
    ]

    # Regex: only accept emails that end exactly at the domain (no .com.au suffix)
    email_pattern = re.compile(
        r'\b([a-zA-Z0-9._%+\-]+@' + re.escape(domain) + r')\b', re.I
    )

    found: set[str] = set()

    for i, query in enumerate(queries, 1):
        print(f"── Query {i}: {query}")

        resp = requests.get(
            SERPAPI_URL,
            params={"q": query, "api_key": SERPAPI_KEY, "num": 5, "engine": "google"},
            timeout=10,
        )

        if not resp.ok:
            print(f"   HTTP {resp.status_code} — skipping\n")
            continue

        organic = resp.json().get("organic_results", [])
        print(f"   Organic results returned: {len(organic)}")

        # Show each result title + snippet so you can see what Google returns
        for j, result in enumerate(organic, 1):
            print(f"\n   Result {j}:")
            print(f"     Title  : {result.get('title', '')}")
            print(f"     Link   : {result.get('link', '')}")
            print(f"     Snippet: {result.get('snippet', '')[:120]}")

        # Dump entire organic results to one string and scan for emails
        raw_text = json.dumps(organic)
        matches_this_query: list[str] = []
        for m in email_pattern.finditer(raw_text):
            email = m.group(1).lower()
            found.add(email)
            matches_this_query.append(email)

        print(f"\n   Emails found in this query: {matches_this_query or 'none'}")

        if found:
            print(f"\n   >> Email found — stopping early (not using remaining queries)\n")
            break

        print()

    # Results summary
    print(f"\n{'='*60}")
    print(f"  ALL EMAILS FOUND : {sorted(found) or 'none'}")
    if found:
        emails = list(found)
        winner = best_email(emails)
        print(f"  SCORES           :")
        for e in sorted(emails, key=email_score, reverse=True):
            print(f"    {email_score(e):3d}  {e}")
        print(f"  BEST EMAIL       : {winner}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    serp_email_search(CLINIC_NAME, CLINIC_DOMAIN)
