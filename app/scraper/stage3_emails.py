"""
STAGE 3 — Six-step email finder (waterfall).

Step 0    quick_scrape()
            Fetches homepage + About/Team/Contact sub-pages with requests.
            Extracts: mailto: links, plain-text emails, doctor names.
            Fast: 2-5 seconds per clinic, no browser.

Step 0.5  npi_owner_lookup()
            Queries the NPPES NPI Registry for the clinic's authorized official
            (owner / CFO / office manager) when no email was found in Step 0.
            Extracts owner name → feeds targeted SMTP patterns into Step 1.
            US clinics only; requires state in the address field. Free, no key.

Step 1    pattern_smtp_search()
            Builds a candidate list:
              - 8 common patterns  (info@, office@, frontdesk@, contact@, ...)
              - 7 name-based patterns per doctor found in Step 0
              - 5 owner-name patterns from NPI Step 0.5 (no dr. prefix)
            SMTP-verifies each candidate. 10-30 seconds per clinic.

Step 2    snov_lookup()
            Snov.io domain email search.
            Free plan: 50 credits/month.
            Fast: 1-2 seconds per clinic (API call).

Step 3    serp_email_search()
            Google SERP email mining via SerpAPI.
            Free plan: 100 searches/month.
            Fast: 1-2 seconds per clinic (API call).

Step 4    browser_find_email()
            browser-use agent navigates the site and clicks tabs.
            Last resort for emails hidden behind JS / contact forms.
            1-2 minutes per clinic.

Step 5    hunter_lookup()
            Queries Hunter.io for all indexed emails at this domain.
            Falls back to email-finder endpoint using doctor names from Step 0.
            Fast: 1-2 seconds per clinic (API call).
            Only runs when HUNTER_API_KEY is configured.
            Paid — only fires when all free steps fail.
"""

import asyncio
import json
import logging
import os
import re
import smtplib
import socket
import sys
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from app.config import get_settings

logger = logging.getLogger(__name__)

try:
    from browser_use import Agent
    from langchain_openai import ChatOpenAI
    HAS_BROWSER = True
except ImportError:
    HAS_BROWSER = False

try:
    import dns.resolver as _dns
    HAS_DNS = True
except ImportError:
    HAS_DNS = False

SMTP_TIMEOUT     = 8    # seconds per SMTP connection
SCRAPE_TIMEOUT   = 8    # seconds per HTTP request
MAX_DOCTORS      = 4    # max doctors to generate patterns for
MODEL            = "llama-3.3-70b-versatile"

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# ── NPI Registry (Step 0.5) ───────────────────────────────────────────────────

_NPI_API_URL = "https://npiregistry.cms.hhs.gov/api/"


def _extract_state_zip(address: str) -> tuple[str | None, str | None]:
    """Parse US state code and 5-digit ZIP from a Google Maps address string."""
    if not address:
        return None, None
    # "Austin, TX 78701" or "Austin, TX 78701-1234"
    m = re.search(r',\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b', address)
    if m:
        return m.group(1), m.group(2)
    # Without comma: "TX 78701"
    m = re.search(r'\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b', address)
    if m:
        return m.group(1), m.group(2)
    # State only, no ZIP
    m = re.search(r',\s*([A-Z]{2})\b', address)
    if m:
        return m.group(1), None
    return None, None


def npi_owner_lookup(clinic_name: str, state: str | None, postal_code: str | None) -> dict:
    """
    Step 0.5 — NPPES NPI Registry lookup for a clinic's authorized official.

    Searches organization_name (prefix wildcard) + enumeration_type=NPI-2 + state.
    Tries with postal_code first (more specific), falls back to state-only.
    Returns the authorized_official fields which represent the clinic's legal
    signing authority: owner, CFO, office manager, or similar decision-maker.

    Returns {"first_name": str|None, "last_name": str|None, "title": str|None}.
    """
    if not clinic_name or not state:
        return {"first_name": None, "last_name": None, "title": None}

    words = clinic_name.strip().split()[:3]
    base_params: dict = {
        "organization_name": " ".join(words) + "*",
        "enumeration_type": "NPI-2",
        "state": state,
        "limit": 5,
        "version": "2.1",
    }

    def _query(params: dict) -> list:
        try:
            resp = requests.get(_NPI_API_URL, params=params, timeout=10)
            if resp.ok:
                return resp.json().get("results", [])
        except Exception:
            pass
        return []

    # Most specific: add postal_code to disambiguate multiple same-name clinics
    if postal_code:
        results = _query({**base_params, "postal_code": postal_code})
        if results:
            basic = results[0].get("basic", {})
            return {
                "first_name": basic.get("authorized_official_first_name"),
                "last_name": basic.get("authorized_official_last_name"),
                "title": basic.get("authorized_official_title_or_position"),
            }

    # Fallback: state only
    results = _query(base_params)
    if results:
        basic = results[0].get("basic", {})
        return {
            "first_name": basic.get("authorized_official_first_name"),
            "last_name": basic.get("authorized_official_last_name"),
            "title": basic.get("authorized_official_title_or_position"),
        }

    return {"first_name": None, "last_name": None, "title": None}


# ── Email quality scoring ─────────────────────────────────────────────────────

_EMAIL_SCORES: dict[str, int] = {
    # Business decision-makers — sign contracts, evaluate vendors
    "ceo":          100,
    "owner":        100,
    "founder":      100,
    "cofounder":     99,
    "president":     98,
    "director":      97,
    "manager":       96,
    "gm":            96,  # general manager
    "principal":     94,
    "partner":       94,
    "vp":            92,
    # Generic business inboxes — unknown who reads them
    "info":          75,
    "office":        75,
    "contact":       70,
    "hello":         65,
    "inquiries":     65,
    "enquiries":     65,
    # Patient-facing / operational — rarely the buyer
    "frontdesk":     45,
    "front":         40,
    "appointments":  35,
    "scheduling":    35,
    "booking":       35,
    "reception":     35,
    "support":       30,
    "admin":         25,
    "billing":       15,
    "accounts":      10,
    # Disqualified
    "webmaster":      0,
    "noreply":        0,
    "donotreply":     0,
    "postmaster":     0,
    "hostmaster":     0,
    "abuse":          0,
}

# Maps cleaned email prefix → human-readable role label
_ROLE_LABELS: dict[str, str] = {
    "ceo":       "CEO",
    "owner":     "Owner",
    "founder":   "Founder / Owner",
    "cofounder": "Co-Founder",
    "president": "President",
    "director":  "Director",
    "manager":   "Manager",
    "gm":        "General Manager",
    "principal": "Principal",
    "partner":   "Partner",
    "vp":        "VP",
}


def _email_priority_score(email: str) -> int:
    """Score an email by how likely it reaches a purchasing decision-maker.
    Higher = better.
      100 — owner / CEO / founder (signs contracts)
       92-98 — director / manager / GM / president / VP (evaluates vendors)
       85 — dr. prefix (dentist — patient-focused, not primary buyer unless also owner)
       75 — info@ / office@ (generic inbox, unknown who reads it)
       35-65 — frontdesk / reception / appointments (patient-facing staff)
       70 — unknown personal name (could be anyone — defaulted high)
        0 — webmaster / noreply / postmaster (disqualified)
    """
    prefix = email.split("@")[0].lower()
    if re.match(r"^dr[.\-]?[a-z]", prefix):
        return 85  # dentist — valuable but not primary purchasing contact
    clean = re.sub(r"[.\-_]", "", prefix)
    for keyword, score in _EMAIL_SCORES.items():
        if re.sub(r"[.\-_]", "", keyword) == clean:
            return score
    return 70  # unknown personal name — could be owner, defaulted high


def _best_email(emails: list[str]) -> str | None:
    """Return highest-scored email from a list, or None if list is empty."""
    if not emails:
        return None
    return max(emails, key=_email_priority_score)


def _infer_role(email: str) -> str | None:
    """Infer the contact's role from the email prefix.
    Returns a human-readable label, or None if the role cannot be determined.
    """
    if not email:
        return None
    prefix = email.split("@")[0].lower()
    if re.match(r"^dr[.\-]?[a-z]", prefix):
        return "Doctor / Dentist"
    clean = re.sub(r"[.\-_]", "", prefix)
    return _ROLE_LABELS.get(clean)

SUBPAGES = [
    "",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/our-team",
    "/team",
    "/staff",
    "/meet-the-doctor",
    "/meet-our-doctors",
    "/meet-the-team",
    "/meet-our-team",
    "/doctors",
    "/providers",
]


# ── logging helpers ───────────────────────────────────────────────────────────

def _log(msg=""):
    logger.debug(msg)

def _banner(text, width=65):
    _log("=" * width)
    _log(f"  {text}")
    _log("=" * width)

def _section(title):
    _log(f"\n  {'-'*60}")
    _log(f"  {title}")
    _log(f"  {'-'*60}")

def _line(label, value, indent=4):
    pad = " " * indent
    _log(f"{pad}{label:<22} {value}")


# ── URL / domain helpers ──────────────────────────────────────────────────────

def _domain_from_url(url):
    if not url:
        return None
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = parsed.netloc or parsed.path
    return re.sub(r"^www\.", "", host).strip("/").split("/")[0] or None

def _base_url(url):
    parsed = urlparse(url if "://" in url else "https://" + url)
    return f"{parsed.scheme}://{parsed.netloc}"


# ── Step 0: quick HTML scrape ─────────────────────────────────────────────────

def _fetch(url):
    """Fetch URL, return (html, status_code, size_kb). html='' on failure."""
    try:
        r = requests.get(url, timeout=SCRAPE_TIMEOUT, headers=SCRAPE_HEADERS,
                         allow_redirects=True)
        size_kb = round(len(r.content) / 1024, 1)
        return (r.text if r.ok else ""), r.status_code, size_kb
    except requests.exceptions.Timeout:
        return "", "TIMEOUT", 0
    except requests.exceptions.ConnectionError:
        return "", "CONN_ERR", 0
    except Exception as e:
        return "", f"ERR({type(e).__name__})", 0


def _mailto_emails(html):
    found = set()
    for m in re.finditer(
        r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
        html, re.I
    ):
        found.add(m.group(1).lower())
    return list(found)


def _text_emails(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ")
    found = set()
    for m in re.finditer(
        r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b', text
    ):
        addr = m.group(1).lower()
        if not re.search(r'\.(png|jpg|gif|svg|css|js|woff)$', addr):
            found.add(addr)
    return list(found)


def _extract_doctor_names(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    found, seen = [], set()
    for pat in [
        r'\bDr\.?\s+([A-Z][a-z]{1,20})\s+([A-Z][a-z]{1,20})\b',
        r'\b([A-Z][a-z]{1,20})\s+([A-Z][a-z]{1,20})\s*,?\s+(?:DDS|DMD|DDS/DMD|DMD/DDS)\b',
    ]:
        for m in re.finditer(pat, text):
            key = (m.group(1).lower(), m.group(2).lower())
            if key not in seen:
                seen.add(key)
                found.append((m.group(1), m.group(2)))
    return found


def _name_patterns(first, last, domain):
    f, l, fi = first.lower(), last.lower(), first[0].lower()
    return [
        f"dr{l}@{domain}",
        f"dr.{l}@{domain}",
        f"dr{f}{l}@{domain}",
        f"dr.{f}.{l}@{domain}",
        f"{fi}{l}@{domain}",
        f"{f}.{l}@{domain}",
        f"{f}{l}@{domain}",
    ]


def _owner_name_patterns(first: str, last: str, domain: str) -> list[str]:
    """SMTP patterns for a non-doctor owner/admin (no dr. prefix)."""
    f, l, fi = first.lower(), last.lower(), first[0].lower()
    return [
        f"{f}.{l}@{domain}",
        f"{fi}{l}@{domain}",
        f"{f}{l}@{domain}",
        f"{f}@{domain}",
        f"{l}@{domain}",
    ]


def quick_scrape(website):
    """
    Step 0: fetch sub-pages, extract emails + doctor names.
    """
    _section("STEP 0 — HTML Scrape  (fast, no browser)")
    _log(f"    Checking {len(SUBPAGES)} sub-pages on {_base_url(website)}")
    _log()

    base        = _base_url(website)
    site_domain = _domain_from_url(website) or ""
    all_emails  = set()
    doctors     = []
    doctor_seen = set()
    pages_hit   = 0

    for sub in SUBPAGES:
        url          = base + sub if sub else website
        short_url    = url.replace(base, "") or "/"
        html, status, size_kb = _fetch(url)

        if not html:
            _log(f"    {short_url:<40} {str(status):<10} (skipped)")
            continue

        pages_hit += 1
        page_emails  = set(_mailto_emails(html) + _text_emails(html))
        page_doctors = _extract_doctor_names(html)

        # Track new finds on this page
        new_emails  = [e for e in page_emails  if e not in all_emails]
        new_doctors = [(f, l) for f, l in page_doctors
                       if (f.lower(), l.lower()) not in doctor_seen]

        all_emails.update(page_emails)
        for f, l in new_doctors:
            doctor_seen.add((f.lower(), l.lower()))
            doctors.append((f, l))

        email_str  = ", ".join(new_emails)  or "none"
        doctor_str = ", ".join(f"Dr. {f} {l}" for f, l in new_doctors) or "none"
        _log(f"    {short_url:<40} {str(status):<6} {size_kb:>6.1f} KB")
        _log(f"      emails   : {email_str}")
        _log(f"      doctors  : {doctor_str}")

    # Sort emails: own-domain first, third-party second
    own, other = [], []
    for e in all_emails:
        if not ("@" in e and "." in e.split("@")[1]):
            continue
        if e.endswith((".png", ".jpg", ".gif", ".svg")):
            continue
        ed = e.split("@")[1]
        if site_domain and (ed == site_domain
                            or site_domain.endswith("." + ed)
                            or ed.endswith("." + site_domain)):
            own.append(e)
        else:
            other.append(e)

    direct_emails = sorted(own) or sorted(other)
    doctors       = doctors[:MAX_DOCTORS]

    _log()
    _log(f"    Step 0 summary:")
    _log(f"      Pages scraped  : {pages_hit}/{len(SUBPAGES)}")
    _log(f"      Direct emails  : {', '.join(direct_emails) if direct_emails else 'none'}")
    _log(f"      Doctors found  : {', '.join('Dr. '+f+' '+l for f,l in doctors) if doctors else 'none'}")

    return {"direct_emails": direct_emails, "doctors": doctors, "pages_hit": pages_hit}


# ── Steps 1+2: pattern build + SMTP verify ────────────────────────────────────

def _common_patterns(domain):
    return [
        f"info@{domain}",
        f"office@{domain}",
        f"frontdesk@{domain}",
        f"contact@{domain}",
        f"hello@{domain}",
        f"admin@{domain}",
        f"appointments@{domain}",
        f"scheduling@{domain}",
    ]


def _mx_host(domain):
    if not HAS_DNS:
        return None
    try:
        records = _dns.resolve(domain, "MX", lifetime=5)
        return str(sorted(records, key=lambda r: r.preference)[0].exchange).rstrip(".")
    except Exception:
        return None


def _smtp_status(email, mx):
    """Returns 'valid' | 'invalid' | 'catch_all' | 'unverifiable'."""
    domain = email.split("@")[1]
    try:
        with smtplib.SMTP(timeout=SMTP_TIMEOUT) as s:
            s.connect(mx, 25)
            s.ehlo("mail.checker.local")
            s.mail("probe@checker.local")
            code_fake, _ = s.rcpt(f"zzz_fake_probe_xyz999@{domain}")
            if code_fake == 250:
                s.rset()
                return "catch_all"
            s.rset()
            s.mail("probe@checker.local")
            code, _ = s.rcpt(email)
            s.rset()
            return "valid" if code == 250 else "invalid"
    except (smtplib.SMTPConnectError, socket.timeout,
            ConnectionRefusedError, OSError):
        return "unverifiable"
    except Exception:
        return "unverifiable"


def pattern_smtp_search(website, doctors=None, owners=None):
    """
    Step 1: build candidate patterns, SMTP-verify each one.
    doctors — list of (first, last) dentist tuples → generates dr. prefix patterns
    owners  — list of (first, last) owner/admin tuples from NPI → no dr. prefix
    """
    domain = _domain_from_url(website)
    if not domain:
        _log("    [smtp] ERROR: could not extract domain from URL")
        return {"email": None, "all_guesses": [], "method": "no_domain"}

    common  = _common_patterns(domain)
    named   = []
    for first, last in (doctors or []):
        for pat in _name_patterns(first, last, domain):
            if pat not in common and pat not in named:
                named.append(pat)
    for first, last in (owners or []):
        for pat in _owner_name_patterns(first, last, domain):
            if pat not in common and pat not in named:
                named.append(pat)

    guesses = common + named

    _section(f"STEP 1+2 — Pattern Build + SMTP Verification")
    _log(f"    Domain   : {domain}")

    mx = _mx_host(domain)
    if not mx:
        _log(f"    MX host  : NOT FOUND — domain cannot receive email, skipping")
        return {"email": None, "all_guesses": guesses, "method": "no_mx"}

    _log(f"    MX host  : {mx}")
    _log(f"    Patterns : {len(guesses)} total  "
         f"({len(common)} common + {len(named)} name-based from {len(doctors or [])} doctor(s))")
    _log()

    _log(f"    {'#':<5} {'Pattern':<45} {'Result'}")
    _log(f"    {'-'*4}  {'-'*44}  {'-'*15}")

    for i, addr in enumerate(guesses, 1):
        kind   = "common" if addr in common else "name  "
        status = _smtp_status(addr, mx)

        result_label = {
            "valid":        "VALID (inbox exists)",
            "invalid":      "invalid",
            "catch_all":    "CATCH-ALL (server accepts all)",
            "unverifiable": "unverifiable (port 25 blocked)",
        }.get(status, status)

        _log(f"    [{i:>2}/{len(guesses)}] ({kind})  {addr:<44} {result_label}")

        if status == "valid":
            _log(f"\n    >> SMTP confirmed valid: {addr}")
            return {"email": addr, "all_guesses": guesses, "method": "smtp_verified"}

        if status == "catch_all":
            _log(f"\n    >> Catch-all server — SMTP cannot distinguish real from fake")
            _log(f"    >> Verifying top guess via Hunter.io: {guesses[0]}")
            result = hunter_verify_email(guesses[0])
            if result in ("deliverable", "risky"):
                return {"email": guesses[0], "all_guesses": guesses, "method": "hunter_verified"}
            _log(f"    >> Hunter could not confirm — skipping to next step")
            return {"email": None, "all_guesses": guesses, "method": "catch_all_unconfirmed"}

        if status == "unverifiable":
            _log(f"\n    >> Port 25 blocked — verifying top guess via Hunter.io: {guesses[0]}")
            result = hunter_verify_email(guesses[0])
            if result in ("deliverable", "risky"):
                return {"email": guesses[0], "all_guesses": guesses, "method": "hunter_verified"}
            _log(f"    >> Hunter could not confirm — skipping to next step")
            return {"email": None, "all_guesses": guesses, "method": "port25_blocked"}

    _log(f"\n    >> All {len(guesses)} patterns rejected by mail server.")
    return {"email": None, "all_guesses": guesses, "method": "smtp_all_invalid"}


# ── Step 2: Snov.io domain search ────────────────────────────────────────────

_SNOV_TOKEN_URL  = "https://api.snov.io/v1/get-access-token"
_SNOV_DOMAIN_URL = "https://api.snov.io/v1/get-domain-emails-with-info"


def _snov_token(client_id: str, client_secret: str) -> str | None:
    try:
        resp = requests.post(
            _SNOV_TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        if resp.ok:
            return resp.json().get("access_token")
    except Exception:
        pass
    return None


def snov_lookup(domain: str) -> dict:
    """
    Step 2 — Snov.io domain email search.
    Free plan: 50 credits/month. Requires SNOV_CLIENT_ID + SNOV_CLIENT_SECRET.
    Returns {"email": str|None, "all_emails": list[str], "method": str}.
    method values: "snov_domain" | "snov_not_found" | "snov_skipped" | "snov_error"
    """
    settings      = get_settings()
    client_id     = settings.snov_client_id or ""
    client_secret = settings.snov_client_secret or ""
    if not client_id or not client_secret:
        _log("    [snov] credentials not configured — skipping")
        return {"email": None, "all_emails": [], "method": "snov_skipped"}

    _section("STEP 2 — Snov.io Domain Search")
    _log(f"    Domain: {domain}")

    token = _snov_token(client_id, client_secret)
    if not token:
        _log("    [snov] failed to obtain access token")
        return {"email": None, "all_emails": [], "method": "snov_error"}

    try:
        resp = requests.post(
            _SNOV_DOMAIN_URL,
            data={"access_token": token, "domain": domain, "type": "all", "limit": 10},
            timeout=10,
        )
        if resp.ok:
            raw    = resp.json().get("emails", [])
            emails = [
                e["email"].lower().strip()
                for e in raw
                if e.get("email") and "@" in e.get("email", "")
            ]
            _log(f"    Snov.io returned {len(emails)} email(s)")
            if emails:
                best = _best_email(emails) or emails[0]
                _log(f"    >> Snov.io found: {best}")
                return {"email": best, "all_emails": emails, "method": "snov_domain"}
        else:
            _log(f"    [snov] HTTP {resp.status_code}")
    except Exception as exc:
        _log(f"    [snov] error: {exc}")

    return {"email": None, "all_emails": [], "method": "snov_not_found"}


# ── Step 3: Google SERP email mining ─────────────────────────────────────────

_SERPAPI_URL = "https://serpapi.com/search.json"


def serp_email_search(clinic_name: str, domain: str) -> dict:
    """
    Step 3 — Google SERP email mining via SerpAPI.
    Free plan: 100 searches/month. Requires SERPAPI_KEY.
    Searches Google for emails indexed at the clinic's domain from any web source.
    Only emails matching the clinic's own domain are returned.
    Returns {"email": str|None, "all_emails": list[str], "method": str}.
    method values: "serp" | "serp_not_found" | "serp_skipped"
    """
    api_key = get_settings().serpapi_key or ""
    if not api_key:
        _log("    [serp] SERPAPI_KEY not configured — skipping")
        return {"email": None, "all_emails": [], "method": "serp_skipped"}

    _section("STEP 3 — Google SERP Email Mining")
    _log(f"    Clinic: {clinic_name}  Domain: {domain}")

    # Three queries; stop as soon as one yields results
    queries = [
        f'"@{domain}"',
        f'"{clinic_name}" email',
        f'site:{domain} contact email',
    ]

    # Negative lookahead (?!\.[a-zA-Z]) stops @domain.com matching inside
    # @domain.com.au — the old \b word-boundary fired between 'm' and '.' there.
    email_pattern = re.compile(
        r'([a-zA-Z0-9._%+\-]+@' + re.escape(domain) + r')(?!\.[a-zA-Z])', re.I
    )
    found: set[str] = set()

    for q in queries:
        try:
            resp = requests.get(
                _SERPAPI_URL,
                params={"q": q, "api_key": api_key, "num": 5, "engine": "google"},
                timeout=10,
            )
            if resp.ok:
                raw_text = json.dumps(resp.json().get("organic_results", []))
                for m in email_pattern.finditer(raw_text):
                    found.add(m.group(1).lower())
                _log(f"    Query [{q[:50]}] → {len(found)} email(s) so far")
                if found:
                    break
            else:
                _log(f"    [serp] HTTP {resp.status_code} for query: {q}")
        except Exception as exc:
            _log(f"    [serp] error: {exc}")

    if found:
        emails = list(found)
        best   = _best_email(emails) or emails[0]
        _log(f"    >> SERP found: {best}")
        return {"email": best, "all_emails": emails, "method": "serp"}

    _log(f"    >> SERP found nothing for {domain}")
    return {"email": None, "all_emails": [], "method": "serp_not_found"}




# ── Step 4: Hunter.io lookup ──────────────────────────────────────────────────

_HUNTER_DOMAIN_URL  = "https://api.hunter.io/v2/domain-search"
_HUNTER_FINDER_URL  = "https://api.hunter.io/v2/email-finder"
_HUNTER_VERIFY_URL  = "https://api.hunter.io/v2/email-verifier"
_HUNTER_MIN_CONFIDENCE = 50  # below this, treat the result as unreliable


def hunter_verify_email(email: str) -> str:
    """
    Verify a single email address via Hunter.io verifier endpoint.
    Costs 1 verification credit per call — only used when SMTP port 25 is blocked.
    Returns: 'deliverable' | 'undeliverable' | 'risky' | 'unknown' | 'skipped' | 'error'
    """
    api_key = get_settings().hunter_api_key or ""
    if not api_key:
        return "skipped"
    try:
        resp = requests.get(
            _HUNTER_VERIFY_URL,
            params={"email": email, "api_key": api_key},
            timeout=15,
        )
        if resp.ok:
            result = resp.json().get("data", {}).get("result", "unknown")
            _log(f"    Hunter verify [{email}] → {result}")
            return result
        _log(f"    Hunter verify HTTP {resp.status_code}")
        return "error"
    except Exception as e:
        _log(f"    Hunter verify error: {e}")
        return "error"


def hunter_lookup(website: str, doctors: list | None = None) -> dict:
    """
    Step 1: query Hunter.io for emails indexed at this domain.
    - Domain search: returns all emails Hunter has found for the domain.
    - Email finder: if doctors were found in Step 0, tries to find their
      specific email using first+last name (limited to 2 doctors to save credits).
    Only runs when HUNTER_API_KEY is configured.
    Returns {
      "email":      str | None,
      "all_emails": [...],
      "method":     "hunter_domain" | "hunter_finder" |
                    "hunter_not_found" | "hunter_skipped" | "hunter_error",
    }
    """
    _section("STEP 1 -- Hunter.io Lookup")

    api_key = get_settings().hunter_api_key or ""
    if not api_key:
        _log("    HUNTER_API_KEY not configured -- skipping")
        return {"email": None, "all_emails": [], "method": "hunter_skipped"}

    domain = _domain_from_url(website)
    if not domain:
        _log("    Could not extract domain from URL")
        return {"email": None, "all_emails": [], "method": "hunter_error"}

    _log(f"    Domain : {domain}")

    # ── Try 1: Domain search ──────────────────────────────────────────────
    _log(f"\n    [1/2] Domain search ...")
    try:
        resp = requests.get(
            _HUNTER_DOMAIN_URL,
            params={"domain": domain, "api_key": api_key},
            timeout=10,
        )
        if resp.ok:
            data = resp.json().get("data", {})
            raw_emails = data.get("emails", [])
            _log(f"    Hunter returned {len(raw_emails)} email(s)")

            candidates = []
            for e in raw_emails:
                val  = (e.get("value") or "").lower().strip()
                conf = e.get("confidence") or 0
                etype = e.get("type") or "?"
                if val and "@" in val:
                    _log(f"      {val:<45} [{etype}]  confidence={conf}")
                    if conf >= _HUNTER_MIN_CONFIDENCE:
                        candidates.append((val, conf))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                best = candidates[0][0]
                _log(f"\n    >> Hunter domain search found: {best}")
                return {
                    "email": best,
                    "all_emails": [e for e, _ in candidates],
                    "method": "hunter_domain",
                }
        else:
            _log(f"    Hunter domain search HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        _log(f"    Hunter domain search error: {e}")

    # ── Try 2: Email finder using doctor names from Step 0 ────────────────
    if doctors:
        _log(f"\n    [2/2] Email finder using {min(len(doctors), 2)} doctor name(s) ...")
        for first, last in (doctors or [])[:2]:
            try:
                resp = requests.get(
                    _HUNTER_FINDER_URL,
                    params={
                        "domain":      domain,
                        "first_name":  first,
                        "last_name":   last,
                        "api_key":     api_key,
                    },
                    timeout=10,
                )
                if resp.ok:
                    data  = resp.json().get("data", {})
                    email = (data.get("email") or "").lower().strip()
                    score = data.get("score") or 0
                    _log(f"    Dr. {first} {last}: {email or 'not found'}  (score={score})")
                    if email and "@" in email and score >= _HUNTER_MIN_CONFIDENCE:
                        _log(f"\n    >> Hunter finder found: {email}")
                        return {
                            "email": email,
                            "all_emails": [email],
                            "method": "hunter_finder",
                        }
            except Exception as e:
                _log(f"    Hunter finder error for Dr. {first} {last}: {e}")

    _log(f"\n    >> Hunter found nothing for {domain}")
    return {"email": None, "all_emails": [], "method": "hunter_not_found"}


# ── Step 3: browser agent ─────────────────────────────────────────────────────

async def browser_find_email(website, clinic_name):
    if not HAS_BROWSER:
        _log("    [browser] browser-use not installed — skipping")
        return {}
    task = (
        f"Go to {website} (the website of dental practice '{clinic_name}'). "
        f"Find a contact email address for the practice. Check the homepage, "
        f"and any 'Contact', 'Contact Us', 'About', or 'Team/Staff' pages. "
        f"Prefer a real practice mailbox (info@, office@, frontdesk@, or a "
        f"named person) over generic webmaster addresses. "
        f"Return ONLY this JSON object, no prose:\n"
        f'{{"email": "<best email or null>", "all_emails": ["..."], '
        f'"source_page": "<url where found or null>", '
        f'"only_contact_form": <true if no email exists, just a form>}}'
    )
    agent = Agent(
        task=task,
        llm=ChatOpenAI(
            model=MODEL,
            api_key=get_settings().groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    )
    history = await agent.run(max_steps=18)
    raw = history.final_result() or ""
    raw = re.sub(r"```(json)?", "", raw).strip()
    m   = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0) if m else raw)
    except Exception:
        return {}


# ── Public API ────────────────────────────────────────────────────────────────

async def find_email_for_clinic(
    website: str,
    clinic_name: str,
    address: str = "",
) -> dict:
    """
    Six-step waterfall — stops as soon as any step finds an email.
    Free steps run first; Hunter.io (paid) only fires when all free steps fail.

    Order:
      0.   HTML scrape     — free, 2-5s
      0.5  NPI owner name  — free, 1-2s   (US only; enriches Step 1 patterns)
      1.   SMTP patterns   — free, 10-30s  (skipped when port 25 is blocked)
      2.   Snov.io         — free, 50/month
      3.   SERP mining     — free, 100/month
      4.   Browser agent   — free (Groq), 1-2 min
      5.   Hunter.io       — paid, 1-2s    (last resort)

    Returns: {email, method, all_emails, doctors_found, contact_role, status}
    """
    email, method, all_emails, doctors = None, None, [], []
    npi_owner: dict = {"first_name": None, "last_name": None, "title": None}

    # Step 0 — HTML scrape (free, fastest)
    try:
        s = quick_scrape(website)
        doctors    = s["doctors"]
        all_emails = s["direct_emails"]
        if s["direct_emails"]:
            email  = _best_email(s["direct_emails"])
            method = "html_direct"
    except Exception:
        pass

    # Step 0.5 — NPI Registry owner lookup (US only; no API key needed)
    # Runs only when Step 0 found no email. Extracts the clinic's authorized
    # official (owner/CFO/manager) from the NPPES database and feeds their
    # name into Step 1's SMTP pattern generator.
    if not email:
        state, postal_code = _extract_state_zip(address)
        if state:
            try:
                npi_owner = await asyncio.to_thread(
                    npi_owner_lookup, clinic_name, state, postal_code
                )
                if npi_owner.get("first_name"):
                    _log(
                        f"    [npi] Found: {npi_owner['first_name']} {npi_owner['last_name']}"
                        f" ({npi_owner.get('title') or 'no title'})"
                    )
            except Exception:
                pass

    # Step 1 — SMTP pattern verify (free, 10-30s; skipped when port 25 blocked)
    if not email:
        try:
            owners = (
                [(npi_owner["first_name"], npi_owner["last_name"])]
                if npi_owner.get("first_name") and npi_owner.get("last_name")
                else []
            )
            fb = pattern_smtp_search(website, doctors=doctors, owners=owners)
            if fb.get("email"):
                email, method = fb["email"], fb["method"]
                all_emails = fb.get("all_guesses", [])
        except Exception:
            pass

    # Step 2 — Snov.io domain search (free 50/month; different DB than Hunter)
    if not email:
        try:
            domain = _domain_from_url(website) or ""
            if domain:
                sr = snov_lookup(domain)
                if sr.get("email"):
                    email, method = sr["email"], sr["method"]
                    all_emails    = sr["all_emails"]
        except Exception:
            pass

    # Step 3 — SERP email mining (free 100/month; Google indexes emails from all web sources)
    if not email:
        try:
            domain = _domain_from_url(website) or ""
            if domain:
                gr = serp_email_search(clinic_name, domain)
                if gr.get("email"):
                    email, method = gr["email"], gr["method"]
                    all_emails    = gr["all_emails"]
        except Exception:
            pass

    # Step 4 — Browser agent (free via Groq, 1-2 min)
    if not email:
        try:
            res = await browser_find_email(website, clinic_name)
            all_emails = res.get("all_emails") or []
            if res.get("email"):
                email, method = res["email"], "browser_scraped"
            else:
                method = "contact_form_only" if res.get("only_contact_form") else "not_found"
        except Exception:
            method = "error"

    # Step 5 — Hunter.io (paid, 1-2s, only when all free steps failed)
    if not email:
        try:
            hr = hunter_lookup(website, doctors=doctors)
            if hr["email"]:
                email, method = hr["email"], hr["method"]
                all_emails = hr["all_emails"]
        except Exception:
            pass

    # Infer role from email prefix; fall back to NPI authorized-official title
    # when the email is a personal name (e.g. jsmith@) with no recognizable prefix.
    role = _infer_role(email)
    if role is None and email and npi_owner.get("title"):
        raw_title = npi_owner["title"].strip()
        role = raw_title.title() if raw_title else None

    return {
        "email": email,
        "method": method or "not_found",
        "all_emails": all_emails,
        "doctors_found": [f"Dr. {f} {l}" for f, l in doctors],
        "contact_role": role,
        "status": "found" if email else "not_found",
    }
