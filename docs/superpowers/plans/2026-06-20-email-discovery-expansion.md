# Email Discovery Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the email discovery waterfall in `app/scraper/stage3_emails.py` to achieve 90%+ email retrieval coverage by adding email quality scoring, Snov.io, Google SERP mining, and US-medical-specific NPI doctor name enrichment.

**Architecture:** Four additions layer on top of the existing 5-step waterfall (HTML → SMTP → Browser → Hunter). Quality scoring is a selector applied at every step. Snov.io and SERP are new paid-free API steps inserted before the slow browser agent. NPI enrichment runs before SMTP so richer doctor names feed into the existing name-pattern generator. All new steps are off-by-default when credentials are absent, so the pipeline degrades gracefully.

**Tech Stack:** Python 3.11, requests, existing `app/config.py` (pydantic-settings), pytest + unittest.mock. External APIs: Snov.io REST (free 50 credits/month), SerpAPI (free 100 searches/month), NPI Registry (free, no key).

## Global Constraints

- Never break existing tests — all 60 tests must still pass after each task.
- All new config fields must be `Optional` (default `None` or `True`) — pipeline must work without any new credentials.
- No new pip dependencies — use only `requests` (already in `pyproject.toml`).
- Follow existing patterns: `_log()` for debug output, `_section()` for step headers, return dicts with `{"email", "all_emails", "method"}`.
- `find_email_for_clinic` is an `async def` — new synchronous helper functions do NOT need `async`.
- Tests live in `tests/test_stage3_emails.py` (new file). Mock all HTTP calls — never hit real APIs in tests.
- Commit after each task.

---

## File Map

| File | Change |
|---|---|
| `app/scraper/stage3_emails.py` | Add scoring helpers, Snov.io, SERP, NPI functions; update pipeline |
| `app/config.py` | Add `snov_client_id`, `snov_client_secret`, `serpapi_key`, `enable_us_medical_lookup` |
| `.env` | Add four new env var stubs (empty values so developer knows they exist) |
| `tests/test_stage3_emails.py` | New test file — all tests for new functions |

---

## Final Pipeline Order (after all tasks)

```
Step 0   HTML scrape           (existing — free, 2-5s)
Step 0.5 NPI enrichment        (NEW — US medical only, free, enriches doctor names before SMTP)
Step 1   SMTP patterns         (existing — free, 10-30s, now benefits from NPI names)
Step 2   Snov.io domain search (NEW — free 50/month, 1-2s)
Step 3   SERP email mining     (NEW — free 100/month, 2-3s)
Step 4   Browser agent         (existing — free via Groq, 1-2 min)
Step 5   Hunter.io             (existing — paid, absolute last resort)
```

Email quality scoring applies at every step to pick the best email when a step finds multiple.

---

### Task 1: Email Quality Scoring

**Files:**
- Modify: `app/scraper/stage3_emails.py`
- Create: `tests/test_stage3_emails.py`

**Interfaces:**
- Produces:
  - `_email_priority_score(email: str) -> int` — used in all later tasks
  - `_best_email(emails: list[str]) -> str | None` — used in all later tasks and in `find_email_for_clinic` Step 0

---

- [ ] **Step 1: Create the test file with failing tests**

Create `tests/test_stage3_emails.py`:

```python
import pytest
from unittest.mock import patch, MagicMock


# ── Quality scoring ───────────────────────────────────────────────────────────

def test_email_priority_score_dr_prefix():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("drsmith@clinic.com") == 100

def test_email_priority_score_dr_dot_prefix():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("dr.jones@clinic.com") == 100

def test_email_priority_score_info():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("info@clinic.com") == 90

def test_email_priority_score_office():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("office@clinic.com") == 90

def test_email_priority_score_frontdesk():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("frontdesk@clinic.com") == 85

def test_email_priority_score_webmaster_is_zero():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("webmaster@clinic.com") == 0

def test_email_priority_score_noreply_is_zero():
    from app.scraper.stage3_emails import _email_priority_score
    assert _email_priority_score("noreply@clinic.com") == 0

def test_email_priority_score_unknown_prefix_returns_70():
    from app.scraper.stage3_emails import _email_priority_score
    # Unknown prefix treated as a personal name — high value
    assert _email_priority_score("jsmith@clinic.com") == 70

def test_best_email_picks_dr_over_info():
    from app.scraper.stage3_emails import _best_email
    emails = ["info@clinic.com", "drjones@clinic.com", "webmaster@clinic.com"]
    assert _best_email(emails) == "drjones@clinic.com"

def test_best_email_picks_info_over_webmaster():
    from app.scraper.stage3_emails import _best_email
    emails = ["webmaster@clinic.com", "info@clinic.com"]
    assert _best_email(emails) == "info@clinic.com"

def test_best_email_returns_none_for_empty_list():
    from app.scraper.stage3_emails import _best_email
    assert _best_email([]) is None

def test_best_email_returns_single():
    from app.scraper.stage3_emails import _best_email
    assert _best_email(["contact@clinic.com"]) == "contact@clinic.com"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_stage3_emails.py -v
```

Expected: FAIL — `ImportError: cannot import name '_email_priority_score'`

- [ ] **Step 3: Add `_EMAIL_SCORES`, `_email_priority_score`, and `_best_email` to `app/scraper/stage3_emails.py`**

Add after the `SCRAPE_HEADERS` block (around line 58), before `SUBPAGES`:

```python
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
```

- [ ] **Step 4: Update Step 0 in `find_email_for_clinic` to use `_best_email`**

In `find_email_for_clinic`, find the Step 0 block:

```python
    # Step 0 — HTML scrape (free, fastest)
    try:
        s = quick_scrape(website)
        doctors    = s["doctors"]
        all_emails = s["direct_emails"]
        if s["direct_emails"]:
            email, method = s["direct_emails"][0], "html_direct"
    except Exception:
        pass
```

Replace with:

```python
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
```

- [ ] **Step 5: Run all tests**

```
pytest tests/ -v
```

Expected: 12 new tests PASS + all 60 existing tests still PASS = 72 total.

- [ ] **Step 6: Commit**

```bash
git add app/scraper/stage3_emails.py tests/test_stage3_emails.py
git commit -m "feat: add email quality scoring — _email_priority_score + _best_email"
```

---

### Task 2: Snov.io Domain Search

**Files:**
- Modify: `app/scraper/stage3_emails.py`
- Modify: `app/config.py`
- Modify: `.env`
- Modify: `tests/test_stage3_emails.py`

**Interfaces:**
- Consumes: `_best_email()` from Task 1, `get_settings()` from `app/config.py`
- Produces: `snov_lookup(domain: str) -> dict` returning `{"email": str|None, "all_emails": list[str], "method": str}`

---

- [ ] **Step 1: Add failing tests to `tests/test_stage3_emails.py`**

Append to the file:

```python
# ── Snov.io ───────────────────────────────────────────────────────────────────

def test_snov_lookup_skipped_when_no_credentials():
    from app.scraper.stage3_emails import snov_lookup
    result = snov_lookup("example.com")
    assert result["email"] is None
    assert result["method"] == "snov_skipped"


def test_snov_lookup_returns_best_email(monkeypatch):
    from app.scraper.stage3_emails import snov_lookup
    from app import config

    # Patch settings to have Snov.io credentials
    mock_settings = MagicMock()
    mock_settings.snov_client_id = "test_id"
    mock_settings.snov_client_secret = "test_secret"
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    token_resp = MagicMock()
    token_resp.ok = True
    token_resp.json.return_value = {"access_token": "tok123"}

    domain_resp = MagicMock()
    domain_resp.ok = True
    domain_resp.json.return_value = {
        "emails": [
            {"email": "webmaster@clinic.com"},
            {"email": "info@clinic.com"},
            {"email": "drsmith@clinic.com"},
        ]
    }

    with patch("requests.post", side_effect=[token_resp, domain_resp]):
        result = snov_lookup("clinic.com")

    assert result["email"] == "drsmith@clinic.com"
    assert result["method"] == "snov_domain"
    assert "info@clinic.com" in result["all_emails"]


def test_snov_lookup_returns_not_found_when_empty(monkeypatch):
    from app.scraper.stage3_emails import snov_lookup
    from app import config

    mock_settings = MagicMock()
    mock_settings.snov_client_id = "test_id"
    mock_settings.snov_client_secret = "test_secret"
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    token_resp = MagicMock()
    token_resp.ok = True
    token_resp.json.return_value = {"access_token": "tok123"}

    domain_resp = MagicMock()
    domain_resp.ok = True
    domain_resp.json.return_value = {"emails": []}

    with patch("requests.post", side_effect=[token_resp, domain_resp]):
        result = snov_lookup("clinic.com")

    assert result["email"] is None
    assert result["method"] == "snov_not_found"


def test_snov_lookup_returns_error_when_token_fails(monkeypatch):
    from app.scraper.stage3_emails import snov_lookup
    from app import config

    mock_settings = MagicMock()
    mock_settings.snov_client_id = "test_id"
    mock_settings.snov_client_secret = "test_secret"
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    token_resp = MagicMock()
    token_resp.ok = False

    with patch("requests.post", return_value=token_resp):
        result = snov_lookup("clinic.com")

    assert result["email"] is None
    assert result["method"] == "snov_error"
```

- [ ] **Step 2: Run new tests to verify they fail**

```
pytest tests/test_stage3_emails.py -k "snov" -v
```

Expected: FAIL — `ImportError: cannot import name 'snov_lookup'`

- [ ] **Step 3: Add Snov.io config fields to `app/config.py`**

In the `Settings` class, after `hunter_api_key`:

```python
    snov_client_id: str | None = None
    snov_client_secret: str | None = None
```

- [ ] **Step 4: Add Snov.io stubs to `.env`**

Append to `.env`:

```
# ── Snov.io (email finder — 50 free credits/month) ───────────────────────────
SNOV_CLIENT_ID=
SNOV_CLIENT_SECRET=
```

- [ ] **Step 5: Add `_snov_token` and `snov_lookup` to `app/scraper/stage3_emails.py`**

Add a new section after the Hunter.io constants block (`_HUNTER_VERIFY_URL` line), before `hunter_verify_email`:

```python
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
```

- [ ] **Step 6: Wire Snov.io into `find_email_for_clinic` as Step 2**

In `find_email_for_clinic`, after the SMTP block (Step 1) and before the browser agent block, add:

```python
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
```

- [ ] **Step 7: Run all tests**

```
pytest tests/ -v
```

Expected: all 4 Snov.io tests PASS + all 72 prior tests PASS = 76 total.

- [ ] **Step 8: Commit**

```bash
git add app/scraper/stage3_emails.py app/config.py .env tests/test_stage3_emails.py
git commit -m "feat: add Snov.io domain email search as Step 2 (free 50/month)"
```

---

### Task 3: Google SERP Email Mining

**Files:**
- Modify: `app/scraper/stage3_emails.py`
- Modify: `app/config.py`
- Modify: `.env`
- Modify: `tests/test_stage3_emails.py`

**Interfaces:**
- Consumes: `_best_email()` from Task 1, `get_settings()` from `app/config.py`
- Produces: `serp_email_search(clinic_name: str, domain: str) -> dict` returning `{"email": str|None, "all_emails": list[str], "method": str}`

---

- [ ] **Step 1: Add failing tests to `tests/test_stage3_emails.py`**

Append to the file:

```python
# ── SERP ──────────────────────────────────────────────────────────────────────

def test_serp_email_search_skipped_when_no_key():
    from app.scraper.stage3_emails import serp_email_search
    result = serp_email_search("Sunny Dental", "sunnydental.com")
    assert result["email"] is None
    assert result["method"] == "serp_skipped"


def test_serp_email_search_finds_email_in_snippet(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app import config

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    serp_resp.json.return_value = {
        "organic_results": [
            {
                "title": "Sunny Dental Contact",
                "snippet": "Contact us at info@sunnydental.com for appointments.",
            }
        ]
    }

    with patch("requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] == "info@sunnydental.com"
    assert result["method"] == "serp"


def test_serp_email_search_picks_best_of_multiple(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app import config

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    serp_resp.json.return_value = {
        "organic_results": [
            {
                "snippet": (
                    "webmaster@sunnydental.com or "
                    "drjones@sunnydental.com or "
                    "info@sunnydental.com"
                )
            }
        ]
    }

    with patch("requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] == "drjones@sunnydental.com"


def test_serp_email_search_returns_not_found_when_no_emails(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app import config

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    serp_resp.json.return_value = {"organic_results": [{"snippet": "No email here."}]}

    with patch("requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] is None
    assert result["method"] == "serp_not_found"


def test_serp_email_search_only_returns_domain_emails(monkeypatch):
    from app.scraper.stage3_emails import serp_email_search
    from app import config

    mock_settings = MagicMock()
    mock_settings.serpapi_key = "test_serp_key"
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    serp_resp = MagicMock()
    serp_resp.ok = True
    # snippet contains an email from a DIFFERENT domain — must not be returned
    serp_resp.json.return_value = {
        "organic_results": [{"snippet": "Contact support@otherdomain.com for help."}]
    }

    with patch("requests.get", return_value=serp_resp):
        result = serp_email_search("Sunny Dental", "sunnydental.com")

    assert result["email"] is None
```

- [ ] **Step 2: Run new tests to verify they fail**

```
pytest tests/test_stage3_emails.py -k "serp" -v
```

Expected: FAIL — `ImportError: cannot import name 'serp_email_search'`

- [ ] **Step 3: Add `serpapi_key` to `app/config.py`**

In the `Settings` class, after `snov_client_secret`:

```python
    serpapi_key: str | None = None
```

- [ ] **Step 4: Add SerpAPI stub to `.env`**

Append to `.env`:

```
# ── SerpAPI (Google SERP email mining — 100 free searches/month) ──────────────
SERPAPI_KEY=
```

- [ ] **Step 5: Add `serp_email_search` to `app/scraper/stage3_emails.py`**

Add after the `snov_lookup` function:

```python
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

    email_pattern = re.compile(
        r'\b([a-zA-Z0-9._%+\-]+@' + re.escape(domain) + r')\b', re.I
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
```

- [ ] **Step 6: Wire SERP into `find_email_for_clinic` as Step 3**

In `find_email_for_clinic`, after the Snov.io block (Step 2) and before the browser agent block, add:

```python
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
```

- [ ] **Step 7: Run all tests**

```
pytest tests/ -v
```

Expected: all 5 SERP tests PASS + all 76 prior tests PASS = 81 total.

- [ ] **Step 8: Commit**

```bash
git add app/scraper/stage3_emails.py app/config.py .env tests/test_stage3_emails.py
git commit -m "feat: add SERP email mining via SerpAPI as Step 3 (free 100/month)"
```

---

### Task 4: US Medical NPI Doctor Enrichment + Niche Guard

**Context:** The NPI (National Provider Identifier) Registry is a free US government API — no key, no rate limit — that lists every licensed dental provider with their full name and practice details. By querying it before the SMTP step, we get authoritative doctor names to feed into the existing name-pattern generator (`_name_patterns`). This improves SMTP hits for US dental clinics without adding any new paid service.

The `enable_us_medical_lookup` config flag (default `True`) lets the operator disable all US-specific behavior with a single env var — e.g. when expanding to non-US markets.

**Files:**
- Modify: `app/scraper/stage3_emails.py`
- Modify: `app/config.py`
- Modify: `.env`
- Modify: `tests/test_stage3_emails.py`

**Interfaces:**
- Consumes: `get_settings()`, `_log()`, `_section()`, `MAX_DOCTORS` constant (already defined in stage3_emails.py as `4`)
- Produces:
  - `_is_us_medical_niche(country: str | None, niche: str | None) -> bool`
  - `npi_doctor_lookup(clinic_name: str, state: str | None = None) -> list[tuple[str, str]]`
  - Updated `find_email_for_clinic(website, clinic_name, country=None, niche=None, state=None)` signature

---

- [ ] **Step 1: Add failing tests to `tests/test_stage3_emails.py`**

Append to the file:

```python
# ── NPI / US medical niche guard ─────────────────────────────────────────────

def test_is_us_medical_niche_true_for_dental_us(monkeypatch):
    from app.scraper.stage3_emails import _is_us_medical_niche
    from app import config

    mock_settings = MagicMock()
    mock_settings.enable_us_medical_lookup = True
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    assert _is_us_medical_niche("US", "dental clinic") is True


def test_is_us_medical_niche_false_when_non_us(monkeypatch):
    from app.scraper.stage3_emails import _is_us_medical_niche
    from app import config

    mock_settings = MagicMock()
    mock_settings.enable_us_medical_lookup = True
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    assert _is_us_medical_niche("UK", "dental clinic") is False


def test_is_us_medical_niche_false_when_non_medical(monkeypatch):
    from app.scraper.stage3_emails import _is_us_medical_niche
    from app import config

    mock_settings = MagicMock()
    mock_settings.enable_us_medical_lookup = True
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    assert _is_us_medical_niche("US", "restaurant") is False


def test_is_us_medical_niche_false_when_disabled(monkeypatch):
    from app.scraper.stage3_emails import _is_us_medical_niche
    from app import config

    mock_settings = MagicMock()
    mock_settings.enable_us_medical_lookup = False
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    # Even though US + dental, the flag is off
    assert _is_us_medical_niche("US", "dental clinic") is False


def test_npi_doctor_lookup_returns_doctor_names(monkeypatch):
    from app.scraper.stage3_emails import npi_doctor_lookup
    from app import config

    mock_settings = MagicMock()
    mock_settings.enable_us_medical_lookup = True
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    npi_resp = MagicMock()
    npi_resp.ok = True
    npi_resp.json.return_value = {
        "results": [
            {"basic": {"authorized_official_first_name": "John", "authorized_official_last_name": "Smith"}},
            {"basic": {"authorized_official_first_name": "Jane", "authorized_official_last_name": "Doe"}},
        ]
    }

    with patch("requests.get", return_value=npi_resp):
        result = npi_doctor_lookup("Sunny Dental", state="CA")

    assert ("John", "Smith") in result
    assert ("Jane", "Doe") in result


def test_npi_doctor_lookup_returns_empty_on_no_results(monkeypatch):
    from app.scraper.stage3_emails import npi_doctor_lookup
    from app import config

    mock_settings = MagicMock()
    mock_settings.enable_us_medical_lookup = True
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    npi_resp = MagicMock()
    npi_resp.ok = True
    npi_resp.json.return_value = {"results": []}

    with patch("requests.get", return_value=npi_resp):
        result = npi_doctor_lookup("Unknown Clinic")

    assert result == []


def test_npi_doctor_lookup_deduplicates_names(monkeypatch):
    from app.scraper.stage3_emails import npi_doctor_lookup
    from app import config

    mock_settings = MagicMock()
    mock_settings.enable_us_medical_lookup = True
    monkeypatch.setattr(config, "get_settings", lambda: mock_settings)

    npi_resp = MagicMock()
    npi_resp.ok = True
    npi_resp.json.return_value = {
        "results": [
            {"basic": {"authorized_official_first_name": "John", "authorized_official_last_name": "Smith"}},
            {"basic": {"authorized_official_first_name": "John", "authorized_official_last_name": "Smith"}},
        ]
    }

    with patch("requests.get", return_value=npi_resp):
        result = npi_doctor_lookup("Sunny Dental")

    assert result.count(("John", "Smith")) == 1
```

- [ ] **Step 2: Run new tests to verify they fail**

```
pytest tests/test_stage3_emails.py -k "npi or us_medical" -v
```

Expected: FAIL — `ImportError: cannot import name '_is_us_medical_niche'`

- [ ] **Step 3: Add `enable_us_medical_lookup` to `app/config.py`**

In the `Settings` class, after `serpapi_key`:

```python
    enable_us_medical_lookup: bool = True  # set False to disable US-specific NPI lookups
```

- [ ] **Step 4: Add US medical stub to `.env`**

Append to `.env`:

```
# ── US Medical (NPI Registry doctor name enrichment) ─────────────────────────
# Set to false when targeting non-US markets
ENABLE_US_MEDICAL_LOOKUP=true
```

- [ ] **Step 5: Add NPI constants, `_is_us_medical_niche`, and `npi_doctor_lookup` to `app/scraper/stage3_emails.py`**

Add after the `serp_email_search` function:

```python
# ── Step 0.5: US medical NPI doctor enrichment ────────────────────────────────

_NPI_API_URL = "https://npiregistry.cms.hhs.gov/api/"

_US_MEDICAL_TERMS = frozenset([
    "dental", "dentist", "dentistry", "clinic", "medical", "healthcare",
    "orthodont", "periodon", "endodon", "oral", "health", "physician",
    "doctor", "therapy", "chiropractic", "optometry", "surgery",
])


def _is_us_medical_niche(country: str | None, niche: str | None) -> bool:
    """True only when country is US AND niche contains a medical/dental term
    AND enable_us_medical_lookup is True in config.
    Set ENABLE_US_MEDICAL_LOOKUP=false in .env to disable for non-US markets.
    """
    if not get_settings().enable_us_medical_lookup:
        return False
    if (country or "").strip().lower() not in ("us", "usa", "united states"):
        return False
    niche_lower = (niche or "").lower()
    return any(term in niche_lower for term in _US_MEDICAL_TERMS)


def npi_doctor_lookup(
    clinic_name: str, state: str | None = None
) -> list[tuple[str, str]]:
    """
    Step 0.5 (US medical only) — Free NPI Registry doctor name enrichment.
    No API key required. Returns up to MAX_DOCTORS (first, last) tuples.
    These are merged with HTML-scraped names and fed into SMTP name patterns.
    """
    _section("STEP 0.5 — NPI Registry Doctor Name Enrichment (US Medical)")
    params: dict[str, str | int] = {
        "version":              "2.1",
        "taxonomy_description": "Dentist",
        "organization_name":    clinic_name,
        "limit":                MAX_DOCTORS,
    }
    if state:
        params["state"] = state.strip().upper()

    try:
        resp = requests.get(_NPI_API_URL, params=params, timeout=8)
        if not resp.ok:
            _log(f"    [npi] HTTP {resp.status_code}")
            return []

        results = resp.json().get("results", [])
        _log(f"    NPI returned {len(results)} result(s) for '{clinic_name}'")

        doctors: list[tuple[str, str]] = []
        seen: set[tuple[str, str]]     = set()

        for r in results:
            basic = r.get("basic", {})
            first = (
                basic.get("authorized_official_first_name")
                or basic.get("first_name")
                or ""
            ).strip()
            last = (
                basic.get("authorized_official_last_name")
                or basic.get("last_name")
                or ""
            ).strip()
            if first and last:
                key = (first.lower(), last.lower())
                if key not in seen:
                    seen.add(key)
                    doctors.append((first, last))
                    _log(f"      Dr. {first} {last}")

        return doctors[:MAX_DOCTORS]
    except Exception as exc:
        _log(f"    [npi] error: {exc}")
        return []
```

- [ ] **Step 6: Update `find_email_for_clinic` signature and wire NPI before SMTP**

Replace the current function signature:

```python
async def find_email_for_clinic(website: str, clinic_name: str) -> dict:
```

With:

```python
async def find_email_for_clinic(
    website: str,
    clinic_name: str,
    country: str | None = None,
    niche: str | None = None,
    state: str | None = None,
) -> dict:
```

Then, after the Step 0 block and before the Step 1 SMTP block, add Step 0.5:

```python
    # Step 0.5 — NPI doctor enrichment (US medical only — enriches doctor names before SMTP)
    if _is_us_medical_niche(country, niche):
        try:
            npi_names = npi_doctor_lookup(clinic_name, state=state)
            existing  = {(f.lower(), l.lower()) for f, l in doctors}
            for first, last in npi_names:
                if (first.lower(), last.lower()) not in existing:
                    doctors.append((first, last))
                    existing.add((first.lower(), last.lower()))
        except Exception:
            pass
```

- [ ] **Step 7: Run all tests**

```
pytest tests/ -v
```

Expected: all 8 NPI tests PASS + all 81 prior tests PASS = 89 total.

- [ ] **Step 8: Commit**

```bash
git add app/scraper/stage3_emails.py app/config.py .env tests/test_stage3_emails.py
git commit -m "feat: add US medical NPI doctor enrichment + niche guard (free, no API key)"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ Email quality scoring — Task 1
- ✅ Snov.io integration (free 50/month) — Task 2
- ✅ SERP email mining (free 100/month) — Task 3
- ✅ US-specific medical directory enrichment (NPI Registry, free, no key) — Task 4
- ✅ US-specific conditional on country=US + medical niche — Task 4 `_is_us_medical_niche`
- ✅ Disable toggle for US-specific behavior — `enable_us_medical_lookup` in config
- ✅ No new pip dependencies — all tasks use only `requests`
- ✅ All new steps degrade gracefully when credentials absent (return `*_skipped`)

**2. Placeholder scan:** None found — all steps contain complete code.

**3. Type consistency:**
- `snov_lookup(domain: str)` — used in Task 2 wiring ✅
- `serp_email_search(clinic_name: str, domain: str)` — used in Task 3 wiring ✅
- `npi_doctor_lookup(clinic_name, state)` — used in Task 4 wiring ✅
- `_best_email(emails: list[str]) -> str | None` — used in Tasks 2, 3 ✅
- `find_email_for_clinic` new params are all `str | None = None` — backwards compatible ✅
