# ReceptAI Backend

Full-stack lead engine for voice-agent outreach to dental clinics (and other medical niches). Scrapes Google Maps → qualifies leads via LLM → finds contact emails → generates personalised cold emails → delivers via Resend → tracks opens/bounces via webhooks.

## Architecture

```
app/
├── main.py                # FastAPI app, lifespan hooks, router registration
├── config.py              # Pydantic BaseSettings (all env vars)
├── database.py            # Supabase singleton (lru_cache)
│
├── core/                  # Shared infrastructure
│   ├── security.py        # JWT (access + refresh), bcrypt
│   ├── middleware.py      # CORS, security headers, rate limiting
│   └── rate_limit.py      # SlowAPI limiter instance
│
├── auth/                  # Authentication
│   ├── router.py          # POST /auth/register, /auth/token, /auth/refresh
│   ├── service.py         # Registration & credential verification
│   ├── models.py          # UserIn, UserOut, TokenResponse
│   └── dependencies.py    # get_current_user dependency
│
├── leads/                 # Lead management
│   ├── router.py          # GET /leads, GET /leads/{id}, PATCH /leads/{id}
│   ├── repository.py      # Supabase queries
│   └── models.py          # LeadOut, LeadStatusUpdate
│
├── scraper/               # 3-stage pipeline
│   ├── router.py          # POST /scraper/jobs, GET /scraper/jobs/{id}
│   ├── pipeline.py        # Orchestrates Stage 1 → 2 → 3
│   ├── stage1_scrape.py   # Google Maps scraping (Camoufox)
│   ├── stage2_categorize.py  # LLM qualification scoring (Groq)
│   ├── stage3_emails.py   # 5-step email discovery
│   └── maps_dom.py        # DOM helpers and selectors
│
├── outreach/              # Cold email campaigns
│   ├── router.py          # /outreach/generate, /campaigns, /webhook/resend
│   ├── email_generator.py # Groq-powered cold email generation
│   ├── campaign_service.py# Bulk generation & send orchestration
│   ├── sender.py          # Resend delivery, plain-text → HTML
│   ├── repository.py      # Campaign/email DB operations
│   └── models.py          # GenerateEmailRequest, CampaignCreate
│
├── cron/                  # Scheduled jobs
│   ├── router.py          # POST /cron/jobs, PATCH /{id}/toggle, DELETE /{id}
│   ├── scheduler.py       # APScheduler singleton
│   ├── jobs.py            # Cron execution handler
│   ├── repository.py      # Cron job CRUD
│   └── models.py          # CronJobCreate
│
└── niches/                # Vertical configuration
    ├── base.py            # NicheConfig dataclass
    └── dental.py          # DentalConfig with personas & pain points
```

**Patterns used:** Feature-based modules, Repository pattern, Dependency Injection (FastAPI `Depends`), Singleton (DB client, APScheduler), Pipeline (Stage 1→2→3), Factory (NicheConfig inheritance).

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI 0.115+ |
| Database | Supabase (PostgreSQL + RLS) |
| Auth | JWT (HS256) + bcrypt |
| Scraping | Camoufox (anti-detect browser), Playwright |
| LLM | Groq API (llama-3.3-70b-versatile) |
| Email Delivery | Resend |
| Scheduling | APScheduler |
| Rate Limiting | SlowAPI |
| Email Enrichment | Apollo.io (optional) |

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package manager)
- A [Supabase](https://supabase.com) project
- [Groq](https://console.groq.com) API key (free tier available)
- [Resend](https://resend.com) API key and a verified sending domain

Install uv if you don't have it:

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Setup

### 1. Clone and enter the project

```bash
git clone <repo-url>
cd dc
```

### 2. Install dependencies

uv creates the virtual environment and installs everything from `pyproject.toml` in one command:

```bash
uv sync
```

To also install dev dependencies (pytest etc.):

```bash
uv sync --dev
```

### 3. Install browser engines

```bash
uv run playwright install chromium
uv run python -m camoufox fetch
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in every value (see [Environment Variables](#environment-variables) below).

### 5. Set up the Supabase database

In your Supabase project, open **SQL Editor** and run the migration:

```
supabase/migrations/001_initial_schema.sql
```

This creates all 10 tables, RLS policies, and seeds the `niches` table with `dental` plus three email templates.

### 6. Run the server

```bash
uv run uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | JWT signing secret — minimum 32 characters |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Access token lifetime (default: `30`) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | Refresh token lifetime (default: `7`) |
| `SUPABASE_URL` | Yes | Your project URL from Supabase dashboard |
| `SUPABASE_ANON_KEY` | Yes | Anon/public key from Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Service role key (used for all DB calls) |
| `GROQ_API_KEY` | Yes | Groq API key for LLM qualification + email generation |
| `RESEND_API_KEY` | Yes | Resend API key for email delivery |
| `FROM_EMAIL` | Yes | Verified sender address (e.g. `outreach@yourdomain.com`) |
| `FROM_NAME` | No | Sender display name (default: `ReceptAI Outreach`) |
| `DEMO_PHONE` | Yes | Phone number inserted into cold email CTAs |
| `DEMO_URL` | Yes | Demo URL inserted into cold email CTAs |
| `BOOKING_URL` | Yes | Booking link inserted into cold email CTAs |
| `APOLLO_API_KEY` | No | Apollo.io key for email enrichment (Stage 3 fallback) |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default: `http://localhost:3000`) |

---

## API Overview

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Create account |
| POST | `/auth/token` | Login — returns access + refresh tokens |
| POST | `/auth/refresh` | Exchange refresh token for new access token |

### Scraper
| Method | Path | Description |
|--------|------|-------------|
| POST | `/scraper/jobs` | Start a new 3-stage scrape pipeline |
| GET | `/scraper/jobs/{job_id}` | Poll job status and progress |
| GET | `/scraper/jobs` | List recent jobs |

### Leads
| Method | Path | Description |
|--------|------|-------------|
| GET | `/leads` | List leads (filter by `niche`, `status`) |
| GET | `/leads/{lead_id}` | Get a single lead with clinic details |
| PATCH | `/leads/{lead_id}` | Update lead status |

### Outreach
| Method | Path | Description |
|--------|------|-------------|
| POST | `/outreach/generate` | Preview a generated cold email |
| POST | `/outreach/campaigns` | Create a campaign |
| GET | `/outreach/campaigns` | List user's campaigns |
| POST | `/outreach/campaigns/{id}/generate` | Bulk-generate draft emails for leads |
| POST | `/outreach/campaigns/{id}/send` | Send all drafts via Resend |
| GET | `/outreach/campaigns/{id}/emails` | List emails in a campaign |
| POST | `/outreach/webhook/resend` | Resend delivery event webhook |

### Cron Jobs
| Method | Path | Description |
|--------|------|-------------|
| POST | `/cron/jobs` | Create a scheduled scrape job |
| GET | `/cron/jobs` | List user's cron jobs |
| GET | `/cron/jobs/{job_id}` | Get a single cron job |
| PATCH | `/cron/jobs/{job_id}/toggle` | Enable or disable a job |
| DELETE | `/cron/jobs/{job_id}` | Delete a cron job |

### Meta
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |

All endpoints except `/auth/*`, `/health`, and `/outreach/webhook/resend` require a `Bearer` token in the `Authorization` header.

---

## Scraper Pipeline

A scrape job triggered via `POST /scraper/jobs` runs three stages asynchronously:

```
Stage 1 — Google Maps scraping (Camoufox)
  ↓  clinic name, address, phone, website, reviews
Stage 2 — LLM qualification (Groq)
  ↓  score 0-10, pain point, complaint quote, personalization hook
Stage 3 — Email discovery (5 steps)
  1. HTML scraping (homepage + 13 subpages)
  2. Pattern build + SMTP verification (info@, office@, ...)
  3. WHOIS registrant lookup
  4. Apollo.io enrichment (optional, requires API key)
  5. Browser-use agent (last resort)
```

Results are persisted to `clinics` → `qualifications` → `leads` tables in real time.

---

## Running Tests

```bash
uv run pytest tests/ -v
```

The test suite mocks Supabase via `conftest.py` fixtures — no real DB connection is needed.

---

## Adding a New Niche

1. Create `app/niches/<niche>.py` extending `NicheConfig`:

```python
from app.niches.base import NicheConfig

class MedSpaConfig(NicheConfig):
    niche_id = "medspa"
    search_term = "medical spas"
    pain_points = ["missed_calls", "no_online_booking"]
    # ... persona, prompts, etc.
```

2. Insert the niche slug into the `niches` table in Supabase.

3. The scraper, cron, and outreach endpoints will automatically accept it via the `niche` parameter.

---

## Security Notes

- All DB access uses the **service role key** (bypasses RLS) — the API layer enforces ownership checks.
- Rate limits are applied per-endpoint (e.g. 3/min for scraper, 10/min for login).
- Security headers (CSP, X-Frame-Options, Permissions-Policy) are injected by `SecurityHeadersMiddleware`.
- Refresh tokens include a `"type": "refresh"` claim to prevent access tokens from being used as refresh tokens.
