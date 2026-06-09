"""
STAGE 3 — Four-step email finder.

Step 0  quick_scrape()
          Fetches homepage + About/Team/Contact sub-pages with requests.
          Extracts: mailto: links, plain-text emails, doctor names (Dr. X Y / X Y DDS|DMD).
          Fast: 2-5 seconds per clinic, no browser.

Step 1+2  pattern_smtp_search()
          Builds a candidate list:
            - 8 common patterns  (info@, office@, frontdesk@, contact@, ...)
            - 7 name-based patterns per doctor found in step 0
              (drsmith@, dr.smith@, jsmith@, j.smith@, john.smith@, ...)
          SMTP-verifies each candidate against the domain's mail server.
          Stops at first confirmed hit. 10-30 seconds per clinic.

Step 4    whois_lookup()
          Queries the domain's WHOIS registration record for the registrant
          email — often the owner's personal or practice address that never
          appears on the website. Filters out privacy-protection addresses.
          Fast: 3-5 seconds per domain.

Step 3    browser_find_email()
          browser-use agent navigates the site and clicks Contact/About/Team tabs.
          Last resort for emails hidden behind JS or contact-form-only pages.
          1-2 minutes per clinic.

Confidence tags written to leads_final.json:
  html_direct          email found in page HTML (mailto: or plain text)  HIGH
  smtp_verified        SMTP handshake confirmed inbox exists              HIGH
  catch_all_guess      server accepts all addresses, info@ returned       MEDIUM
  port25_blocked_guess ISP blocks port 25, info@ returned unverified      LOW
  whois_registrant     found in WHOIS domain registration record          MEDIUM
  browser_scraped      browser agent scraped it                           HIGH
  not_found / error    all steps failed

Setup:  pip install "browser-use" langchain-groq dnspython requests beautifulsoup4 python-whois
        set GROQ_API_KEY=gsk_...
Run:    python find_emails.py
"""

import asyncio
import json
import os
import re
import smtplib
import socket
import sys
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

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

INPUT            = "clinics_qualified.json"
OUTPUT           = "leads_final.json"
CHECKPOINT       = "checkpoint_emails.json"
MODEL            = "llama-3.3-70b-versatile"
ONLY_RECOMMENDED = False   # set True to only process clinics scored >= 6 by Stage 2
SMTP_TIMEOUT     = 8    # seconds per SMTP connection
SCRAPE_TIMEOUT   = 8    # seconds per HTTP request
MAX_DOCTORS      = 4    # max doctors to generate patterns for

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
    print(msg, flush=True)

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
    Logs every URL attempt with HTTP status and what was found per page.
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

    # Print all patterns upfront so user can see what will be tried
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
        sys.stdout.flush()

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
            api_key=os.environ["GROQ_API_KEY"],
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


# ── checkpoint helpers ────────────────────────────────────────────────────────

def _load_ckpt():
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT, encoding="utf-8") as fh:
            return json.load(fh)
    return {}

def _save_ckpt(d):
    with open(CHECKPOINT, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)


# ── main ─────────────────────────────────────────────────────────────────────

async def main_async():
    with open(INPUT, encoding="utf-8") as fh:
        data = json.load(fh)
    clinics = data.get("clinics", [])
    done    = _load_ckpt()

    stats = {"found": 0, "not_found": 0, "skipped": 0}

    for i, c in enumerate(clinics, 1):
        name    = c["clinic"].get("name")
        website = c["clinic"].get("website")
        rec     = c["qualification"].get("recommend_outreach")
        key     = c["clinic"].get("google_maps_url") or name

        if key in done:
            _log(f"\n[skip] ({i}/{len(clinics)}) {name}  (already in checkpoint)")
            continue

        if ONLY_RECOMMENDED and not rec:
            _log(f"\n[skip] ({i}/{len(clinics)}) {name}  (not recommended)")
            done[key] = {"status": "skipped_not_recommended"}
            stats["skipped"] += 1
            continue

        if not website:
            _log(f"\n[skip] ({i}/{len(clinics)}) {name}  (no website)")
            done[key] = {"status": "no_website"}
            stats["skipped"] += 1
            _save_ckpt(done)
            continue

        # ── clinic header ─────────────────────────────────────────────────
        _log()
        _banner(f"[3] Clinic {i}/{len(clinics)}: {name}")
        _line("Website", website)
        _line("Recommended", "YES" if rec else "NO")

        email       = None
        method      = None
        all_emails  = []
        source_page = None
        only_form   = False
        doctors     = []

        # ── STEP 0: quick HTML scrape ─────────────────────────────────────
        try:
            scraped    = quick_scrape(website)
            doctors    = scraped["doctors"]
            all_emails = scraped["direct_emails"]

            if scraped["direct_emails"]:
                email  = scraped["direct_emails"][0]
                method = "html_direct"
        except Exception as e:
            _log(f"    [step 0] ERROR: {e}")

        # ── STEPS 1+2: pattern + SMTP ─────────────────────────────────────
        if not email:
            try:
                fb = pattern_smtp_search(website, doctors=doctors)
                all_emails = fb.get("all_guesses", [])
                if fb.get("email"):
                    email  = fb["email"]
                    method = fb["method"]
            except Exception as e:
                _log(f"    [step 1+2] ERROR: {e}")

        # ── STEP 4: WHOIS lookup ──────────────────────────────────────────
        if not email:
            try:
                wr = whois_lookup(website)
                if wr["emails"]:
                    email      = wr["emails"][0]
                    all_emails = wr["emails"]
                    method     = wr["method"]
            except Exception as e:
                _log(f"    [step 4] ERROR: {e}")
        else:
            _section("STEP 4 -- WHOIS Lookup")
            _log(f"    Skipped — email already found in earlier step.")

        # ── STEP 3: browser agent (last resort) ───────────────────────────
        if not email:
            _section("STEP 3 — Browser Agent  (last resort, slow)")
            _log(f"    Steps 0, 1+2, and 4 all found nothing.")
            _log(f"    Launching browser agent to navigate the site ...")
            _log()
            try:
                res         = await browser_find_email(website, name)
                all_emails  = res.get("all_emails") or []
                source_page = res.get("source_page")
                only_form   = res.get("only_contact_form", False)
                if res.get("email"):
                    email  = res["email"]
                    method = "browser_scraped"
                    _log(f"    Browser agent found: {email}  (source: {source_page})")
                else:
                    method = "contact_form_only" if only_form else "not_found"
                    _log(f"    Browser agent found nothing  ({method})")
            except Exception as e:
                _log(f"    [step 3] ERROR: {e}")
                method = method or "error"
        else:
            _section("STEP 3 — Browser Agent")
            _log(f"    Skipped — email already found in earlier step.")

        # ── per-clinic result ─────────────────────────────────────────────
        status = "found" if email else "not_found"
        if status == "found":
            stats["found"] += 1
        else:
            stats["not_found"] += 1

        _section("RESULT")
        _line("Status",  status.upper())
        _line("Email",   email or "none")
        _line("Method",  method or "n/a")
        _line("Doctors", ", ".join(f"Dr. {f} {l}" for f, l in doctors) or "none")
        if source_page:
            _line("Source page", source_page)

        done[key] = {
            "email":             email,
            "all_emails":        all_emails,
            "source_page":       source_page,
            "only_contact_form": only_form,
            "doctors_found":     [f"Dr. {f} {l}" for f, l in doctors],
            "method":            method,
            "status":            status,
        }
        _save_ckpt(done)

    # ── merge + write final output ────────────────────────────────────────
    enriched = 0
    for c in clinics:
        key = c["clinic"].get("google_maps_url") or c["clinic"].get("name")
        c["enrichment"] = done.get(key, {"status": "skipped"})
        if c["enrichment"].get("email"):
            enriched += 1

    result = {
        "run_meta": {
            "stage": "find_emails", "model": MODEL,
            "clinics": len(clinics), "clinics_with_email": enriched,
        },
        "leads": clinics,
    }
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    total = len(clinics)
    _log()
    _banner("PIPELINE COMPLETE")
    _line("Output file",  OUTPUT)
    _line("Total clinics", str(total))
    _line("Emails found",  f"{stats['found']}  ({round(stats['found']/max(total,1)*100)}%)")
    _line("Not found",     str(stats['not_found']))
    _line("Skipped",       str(stats['skipped']))
    _log("=" * 65)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
