"""
Apollo.io API test — find emails for penndentalmedicine.org
Run: python test_apollo.py
"""
import json
import re
import requests
from bs4 import BeautifulSoup

APOLLO_API_KEY = "SGRDTY07IbGa87dp91TtNg"
DOMAIN = "penndentalmedicine.org"
BASE = "https://api.apollo.io/v1"
HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "X-Api-Key": APOLLO_API_KEY,
}


def _print(label, data):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)
    print(json.dumps(data, indent=2))


# ── Step 1: Org enrich (get company overview + generic emails) ────────────────

def org_enrich():
    print("\n[1] Organization enrich ...")
    r = requests.post(
        f"{BASE}/organizations/enrich",
        headers=HEADERS,
        json={"domain": DOMAIN},
    )
    data = r.json()
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {data}")
        return None
    org = data.get("organization", {})
    emails = []
    # Top-level email fields
    for field in ("email", "sanitized_phone"):
        if org.get(field):
            emails.append(org[field])
    _print(f"Org: {org.get('name')} | Found fields", {
        "name": org.get("name"),
        "website": org.get("website_url"),
        "primary_phone": org.get("primary_phone"),
        "linkedin": org.get("linkedin_url"),
        "employee_count": org.get("estimated_num_employees"),
    })
    return org


# ── Step 2: People search — find contacts at the domain ──────────────────────

def people_search():
    print("\n[2] People search at domain ...")
    r = requests.post(
        f"{BASE}/mixed_people/search",
        headers=HEADERS,
        json={
            "q_organization_domains": DOMAIN,
            "page": 1,
            "per_page": 10,
            "contact_email_status": ["verified", "guessed", "unavailable",
                                      "bounced", "pending_manual_fulfillment"],
        },
    )
    data = r.json()
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {data}")
        return []

    people = data.get("people", []) or data.get("contacts", [])
    print(f"    Found {len(people)} people  (total: {data.get('pagination', {}).get('total_entries', '?')})")

    emails_found = []
    for p in people:
        email = p.get("email") or p.get("personal_email") or ""
        name  = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        title = p.get("title", "")
        print(f"    • {name:<30} {title:<35} {email}")
        if email and "@" in email:
            emails_found.append({"name": name, "title": title, "email": email})

    return emails_found


# ── Step 3: Contacts search (sometimes free-tier accessible) ──────────────────

def contacts_search():
    print("\n[3] Contacts search ...")
    r = requests.post(
        f"{BASE}/contacts/search",
        headers=HEADERS,
        json={
            "q_organization_domains": DOMAIN,
            "page": 1,
            "per_page": 10,
        },
    )
    data = r.json()
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {data.get('error', data)}")
        return []

    contacts = data.get("contacts", [])
    print(f"    Found {len(contacts)} contacts")
    emails_found = []
    for p in contacts:
        email = p.get("email") or ""
        name  = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        title = p.get("title", "")
        print(f"    • {name:<30} {title:<35} {email}")
        if email and "@" in email:
            emails_found.append({"name": name, "title": title, "email": email})
    return emails_found


# ── Step 4: People match — enrich a guessed email pattern ─────────────────────

def people_match(org_id=None):
    """Try to match people at the domain using common name patterns."""
    print("\n[4] People match (enrich by domain) ...")
    payload = {"domain": DOMAIN, "reveal_personal_emails": False}
    if org_id:
        payload["organization_id"] = org_id
    r = requests.post(f"{BASE}/people/match", headers=HEADERS, json=payload)
    data = r.json()
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {data.get('error', data)}")
        return []
    person = data.get("person", {})
    if not person:
        print("    No match found.")
        return []
    email = person.get("email", "")
    name  = f"{person.get('first_name','')} {person.get('last_name','')}".strip()
    title = person.get("title", "")
    print(f"    • {name:<30} {title:<35} {email}")
    return [{"name": name, "title": title, "email": email}] if email and "@" in email else []


# ── Step 5: Direct website scrape for mailto: and visible emails ──────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_JUNK = re.compile(r"(example|sentry|wix|schema|pixel|@2x|\.png|\.jpg|\.svg|"
                   r"noreply|no-reply|test@|sample@)", re.I)

def scrape_website():
    print("\n[5] Direct website scrape for email addresses ...")
    found = set()
    urls_to_try = [
        f"https://{DOMAIN}",
        f"https://{DOMAIN}/contact",
        f"https://{DOMAIN}/contact-us",
        f"https://{DOMAIN}/about",
        f"https://{DOMAIN}/about-us",
        f"https://www.{DOMAIN}",
    ]
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    for url in urls_to_try:
        try:
            r = session.get(url, timeout=10, allow_redirects=True)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # mailto: links
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("mailto:"):
                    email = href[7:].split("?")[0].strip()
                    if email and not _JUNK.search(email):
                        found.add(email)
                        print(f"    mailto: {email}  (from {url})")
            # plain text patterns
            for email in _EMAIL_RE.findall(r.text):
                if DOMAIN.split(".")[0] in email.lower() or "upenn" in email.lower():
                    if not _JUNK.search(email):
                        found.add(email)
                        print(f"    text:   {email}  (from {url})")
        except Exception as exc:
            print(f"    {url} — {exc}")

    return [{"name": "", "title": "website", "email": e} for e in found]


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nApollo.io email finder — domain: {DOMAIN}")
    print(f"API key: {APOLLO_API_KEY[:8]}...")

    org      = org_enrich()
    org_id   = (org or {}).get("id")
    contacts = people_search()
    ct2      = contacts_search()
    pm       = people_match(org_id)
    scraped  = scrape_website()

    # Merge + deduplicate
    all_emails = {e["email"]: e for e in (contacts + ct2 + pm + scraped) if e.get("email")}.values()

    print("\n" + "="*60)
    print("  RESULTS")
    print("="*60)
    if all_emails:
        for e in all_emails:
            print(f"  OK  {e['email']:<45}  {e['name']}  ({e['title']})")
    else:
        print("  No emails found via Apollo for this domain.")
        print("  The org may be unlisted, or the free-tier quota may be exhausted.")
