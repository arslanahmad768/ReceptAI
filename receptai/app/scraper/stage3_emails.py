"""
STAGE 3 — Five-step email finder (module version).

Step 0    quick_scrape()
            Fetches homepage + About/Team/Contact sub-pages with requests.
            Extracts: mailto: links, plain-text emails, doctor names.
            Fast: 2-5 seconds per clinic, no browser.

Step 1+2  pattern_smtp_search()
            Builds a candidate list:
              - 8 common patterns  (info@, office@, frontdesk@, contact@, ...)
              - 7 name-based patterns per doctor found in step 0
            SMTP-verifies each candidate. 10-30 seconds per clinic.

Step 4    whois_lookup()
            Queries WHOIS registration record for the registrant email.
            Filters out registrar / privacy-service addresses.
            Fast: 3-5 seconds per domain.

Step 5    apollo_lookup()
            Calls Apollo.io People Search + Org Enrich APIs.
            Finds emails that never appear on the website or WHOIS record.
            Only runs when apollo_api_key is configured.

Step 3    browser_find_email()
            browser-use agent navigates the site and clicks tabs.
            Last resort for emails hidden behind JS / contact forms.
            1-2 minutes per clinic.
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
    import whois as _whois
    HAS_WHOIS = True
except ImportError:
    HAS_WHOIS = False

from browser_use import Agent
from langchain_openai import ChatOpenAI

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


def pattern_smtp_search(website, doctors=None):
    """
    Steps 1+2: build candidate patterns, SMTP-verify each one.
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
            _log(f"\n    >> Catch-all server — returning best guess: {guesses[0]}")
            return {"email": guesses[0], "all_guesses": guesses, "method": "catch_all_guess"}

        if status == "unverifiable":
            _log(f"\n    >> Port 25 blocked by ISP — returning unverified guess: {guesses[0]}")
            return {"email": guesses[0], "all_guesses": guesses, "method": "port25_blocked_guess"}

    _log(f"\n    >> All {len(guesses)} patterns rejected by mail server.")
    return {"email": None, "all_guesses": guesses, "method": "smtp_all_invalid"}


# ── Step 4: WHOIS lookup ─────────────────────────────────────────────────────

# Prefixes used by registrars/hosts, not actual business owners
_WHOIS_BAD_PREFIXES = (
    "abuse", "hostmaster", "postmaster", "webmaster",
    "domain.operations", "noreply", "no-reply", "support",
    "admin", "info", "contact",           # too generic from registrar sections
)

# Registrar / hosting / privacy-service domains — never the actual owner
_WHOIS_BAD_DOMAINS = {
    "godaddy.com", "squarespace.com", "web.com", "namecheap.com",
    "networksolutions.com", "tucows.com", "enom.com", "hover.com",
    "dreamhost.com", "bluehost.com", "hostgator.com", "ionos.com",
    "register.com", "name.com", "dynadot.com", "porkbun.com",
    "cloudflare.com", "amazonaws.com", "google.com", "microsoft.com",
    "whoisguard.com", "domainsbyproxy.com", "whoisprivacy.com",
    "myprivatename.com", "privacyprotect.org", "contactprivacy.com",
    "withheldforprivacy.com", "identity.digital",
}

# Keywords anywhere in the address that signal privacy/proxy
_WHOIS_PRIVACY_KEYWORDS = (
    "privacy", "proxy", "guard", "protect", "redacted",
    "withheld", "noreply", "no-reply",
)


def _is_whois_junk(email):
    """Return True if this email is a registrar/host/privacy address, not the owner."""
    low   = email.lower()
    local = low.split("@")[0]
    dom   = low.split("@")[1] if "@" in low else ""

    if dom in _WHOIS_BAD_DOMAINS:
        return True
    if any(kw in low for kw in _WHOIS_PRIVACY_KEYWORDS):
        return True
    if any(local.startswith(pfx) for pfx in _WHOIS_BAD_PREFIXES):
        return True
    # Random-string privacy proxies (e.g. vd59s2pk4ud@myprivatename.com)
    # heuristic: local part 8+ chars with 3+ digits
    if len(local) >= 8 and sum(c.isdigit() for c in local) >= 3:
        return True
    return False


def whois_lookup(website):
    """
    Step 4: query WHOIS registration record for the domain.
    Returns {
      "emails":  [real registrant emails, privacy addresses filtered out],
      "method":  "whois_registrant" | "whois_private" | "whois_no_email" |
                 "whois_error" | "whois_unavailable",
    }
    """
    _section("STEP 4 -- WHOIS Domain Registration Lookup")

    if not HAS_WHOIS:
        _log("    python-whois not installed -- skipping (pip install python-whois)")
        return {"emails": [], "method": "whois_unavailable"}

    domain = _domain_from_url(website)
    if not domain:
        _log("    Could not extract domain from URL")
        return {"emails": [], "method": "whois_error"}

    _log(f"    Domain  : {domain}")
    _log(f"    Querying WHOIS registry ...")

    try:
        w = _whois.whois(domain)
    except Exception as e:
        _log(f"    WHOIS query failed: {e}")
        return {"emails": [], "method": "whois_error"}

    # python-whois returns emails as a string, list, or None
    raw = w.emails or []
    if isinstance(raw, str):
        raw = [raw]
    raw = [e.lower().strip() for e in raw if e and "@" in e]

    _log(f"    Raw emails in record : {raw if raw else 'none'}")

    real    = [e for e in raw if not _is_whois_junk(e)]
    private = [e for e in raw if     _is_whois_junk(e)]

    if real:
        _log(f"    Registrant emails    : {', '.join(real)}")
        _log(f"    Privacy emails (skip): {', '.join(private) or 'none'}")
        _log(f"\n    >> WHOIS found: {real[0]}")
        return {"emails": real, "method": "whois_registrant"}

    if private:
        _log(f"    Registrant email hidden (registrar/privacy-service addresses only)")
        _log(f"    Privacy addresses    : {', '.join(private)}")
        return {"emails": [], "method": "whois_private"}

    _log(f"    No email address in WHOIS record")
    return {"emails": [], "method": "whois_no_email"}


# ── Step 5: Apollo.io lookup ──────────────────────────────────────────────────

_APOLLO_PEOPLE_URL = "https://api.apollo.io/api/v1/mixed_people/search"
_APOLLO_ORG_URL    = "https://api.apollo.io/api/v1/organizations/enrich"

# Titles most likely to own / manage a small dental practice
_APOLLO_TITLES = [
    "owner", "dentist", "dds", "dmd",
    "office manager", "practice manager", "managing director",
    "director", "principal", "founder",
]


def apollo_lookup(website, clinic_name):
    """
    Step 5: query Apollo.io for people / org emails at this domain.
    Only runs when apollo_api_key is configured.
    Returns {
      "emails":  [...],
      "people":  [{"name", "title", "email"}, ...],
      "method":  "apollo" | "apollo_not_found" | "apollo_skipped" | "apollo_error",
    }
    """
    _section("STEP 5 -- Apollo.io Lookup")

    api_key = get_settings().apollo_api_key or ""
    if not api_key:
        _log("    apollo_api_key not configured -- skipping")
        return {"emails": [], "people": [], "method": "apollo_skipped"}

    domain = _domain_from_url(website)
    if not domain:
        _log("    Could not extract domain -- skipping")
        return {"emails": [], "people": [], "method": "apollo_error"}

    _log(f"    Domain  : {domain}")
    _log(f"    Clinic  : {clinic_name}")

    found_emails = []
    found_people = []

    # ── Try 1: People Search ─────────────────────────────────────────────
    _log(f"\n    [1/2] People search at {domain} ...")
    try:
        resp = requests.post(
            _APOLLO_PEOPLE_URL,
            json={
                "api_key":                  api_key,
                "organization_domains":     [domain],
                "person_titles":            _APOLLO_TITLES,
                "contact_email_status":     ["verified", "likely to engage"],
                "page":                     1,
                "per_page":                 5,
            },
            timeout=15,
        )
        if resp.ok:
            people = resp.json().get("people", [])
            _log(f"    People returned : {len(people)}")
            for p in people:
                first = p.get("first_name", "") or ""
                last  = p.get("last_name",  "") or ""
                name  = f"{first} {last}".strip()
                title = p.get("title", "") or ""
                email = (p.get("email") or "").lower().strip()
                # Apollo masks unverified emails as "email@domain.com" with "?" chars
                if email and "@" in email and "?" not in email:
                    _log(f"      + {name:<25} ({title:<30})  {email}")
                    found_emails.append(email)
                    found_people.append({"name": name, "title": title, "email": email})
                else:
                    masked = email or "no email"
                    _log(f"      - {name:<25} ({title:<30})  {masked}  [masked/unavailable]")
        else:
            _log(f"    Apollo people search HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        _log(f"    Apollo people search error: {e}")

    # ── Try 2: Org Enrich (if people search found nothing) ───────────────
    if not found_emails:
        _log(f"\n    [2/2] Organisation enrich for {domain} ...")
        try:
            resp = requests.post(
                _APOLLO_ORG_URL,
                json={"api_key": api_key, "domain": domain},
                timeout=15,
            )
            if resp.ok:
                org = resp.json().get("organization") or {}
                # Apollo puts generic contact emails under various keys
                for key in ("contact_emails", "emails"):
                    for ce in (org.get(key) or []):
                        ce = str(ce).lower().strip()
                        if "@" in ce and "?" not in ce:
                            _log(f"    Org email found: {ce}")
                            found_emails.append(ce)
                if not found_emails:
                    _log(f"    No usable email in org record")
            else:
                _log(f"    Apollo org enrich HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            _log(f"    Apollo org enrich error: {e}")

    if found_emails:
        _log(f"\n    >> Apollo found: {found_emails[0]}")
        return {"emails": found_emails, "people": found_people, "method": "apollo"}

    _log(f"\n    >> Apollo found nothing for this domain")
    return {"emails": [], "people": found_people, "method": "apollo_not_found"}


# ── Step 3: browser agent ─────────────────────────────────────────────────────

async def browser_find_email(website, clinic_name):
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

async def find_email_for_clinic(website: str, clinic_name: str) -> dict:
    """
    Run all 5 steps in priority order.
    Returns: {email, method, all_emails, doctors_found, status}
    """
    email, method, all_emails, doctors = None, None, [], []

    # Step 0 — HTML scrape (fastest)
    try:
        s = quick_scrape(website)
        doctors = s["doctors"]
        all_emails = s["direct_emails"]
        if s["direct_emails"]:
            email, method = s["direct_emails"][0], "html_direct"
    except Exception:
        pass

    # Steps 1+2 — Pattern + SMTP
    if not email:
        try:
            fb = pattern_smtp_search(website, doctors=doctors)
            all_emails = fb.get("all_guesses", [])
            if fb.get("email"):
                email, method = fb["email"], fb["method"]
        except Exception:
            pass

    # Step 4 — WHOIS
    if not email:
        try:
            wr = whois_lookup(website)
            if wr["emails"]:
                email, method = wr["emails"][0], wr["method"]
                all_emails = wr["emails"]
        except Exception:
            pass

    # Step 5 — Apollo
    if not email:
        try:
            ar = apollo_lookup(website, clinic_name)
            if ar["emails"]:
                email, method = ar["emails"][0], ar["method"]
                all_emails = ar["emails"]
        except Exception:
            pass

    # Step 3 — Browser agent (last resort, slow)
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

    return {
        "email": email,
        "method": method or "not_found",
        "all_emails": all_emails,
        "doctors_found": [f"Dr. {f} {l}" for f, l in doctors],
        "status": "found" if email else "not_found",
    }
