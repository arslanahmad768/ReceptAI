"""
STAGE 3 — Four-step email finder (waterfall).

Step 0    quick_scrape()
            Fetches homepage + About/Team/Contact sub-pages with requests.
            Extracts: mailto: links, plain-text emails, doctor names.
            Fast: 2-5 seconds per clinic, no browser.

Step 1    pattern_smtp_search()
            Builds a candidate list:
              - 8 common patterns  (info@, office@, frontdesk@, contact@, ...)
              - 7 name-based patterns per doctor found in step 0
            SMTP-verifies each candidate. 10-30 seconds per clinic.

Step 2    browser_find_email()
            browser-use agent navigates the site and clicks tabs.
            Last resort for emails hidden behind JS / contact forms.
            1-2 minutes per clinic.

Step 3    hunter_lookup()
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

# ── Email quality scoring ─────────────────────────────────────────────────────

_EMAIL_SCORES: dict[str, int] = {
    "info":          90,
    "office":        90,
    "frontdesk":     85,
    "front":         80,
    "contact":       80,
    "hello":         75,
    "inquiries":     75,
    "enquiries":     75,
    "appointments":  70,
    "scheduling":    70,
    "booking":       70,
    "reception":     70,
    "support":       50,
    "admin":         40,
    "billing":       35,
    "accounts":      30,
    "webmaster":      0,
    "noreply":        0,
    "noreply":        0,
    "donotreply":     0,
    "postmaster":     0,
    "hostmaster":     0,
    "abuse":          0,
}


def _email_priority_score(email: str) -> int:
    """Score an email address by how likely it reaches a decision-maker.
    Higher = better. dr. prefix = 100, info/office = 90, webmaster = 0.
    """
    prefix = email.split("@")[0].lower()
    if re.match(r"^dr[.\-]?[a-z]", prefix):
        return 100
    clean = re.sub(r"[.\-_]", "", prefix)
    for keyword, score in _EMAIL_SCORES.items():
        if re.sub(r"[.\-_]", "", keyword) == clean:
            return score
    return 70  # unknown prefix — treat as personal name, high value


def _best_email(emails: list[str]) -> str | None:
    """Return highest-scored email from a list, or None if list is empty."""
    if not emails:
        return None
    return max(emails, key=_email_priority_score)

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


# ── Step 3: Hunter.io lookup ──────────────────────────────────────────────────

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
    Five-step waterfall — stops as soon as any step finds an email.
    Free steps run first; Hunter.io (paid) only fires when all free steps fail.

    Order:
      0. HTML scrape     — free, 2-5s
      1. SMTP patterns   — free, 10-30s  (skipped when port 25 is blocked)
      2. Snov.io         — free, 50/month
      3. Browser agent   — free (Groq), 1-2 min
      4. Hunter.io       — paid, 1-2s    (last resort — only when all free steps fail)

    Returns: {email, method, all_emails, doctors_found, status}
    """
    email, method, all_emails, doctors = None, None, [], []

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

    # Step 1 — SMTP pattern verify (free, 10-30s; skipped when port 25 blocked)
    if not email:
        try:
            fb = pattern_smtp_search(website, doctors=doctors)
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

    # Step 3 — Browser agent (free via Groq, 1-2 min)
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

    # Step 4 — Hunter.io (paid, 1-2s, only when all free steps failed)
    if not email:
        try:
            hr = hunter_lookup(website, doctors=doctors)
            if hr["email"]:
                email, method = hr["email"], hr["method"]
                all_emails = hr["all_emails"]
        except Exception:
            pass

    return {
        "email": email,
        "method": method or "not_found",
        "all_emails": all_emails,
        "doctors_found": [f"Dr. {f} {l}" for f, l in doctors],
        "status": "found" if email else "not_found",
    }
