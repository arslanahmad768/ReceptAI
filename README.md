# ReceptAI Backend

A FastAPI lead engine for AI voice-agent outreach to dental clinics. It discovers clinics on Google Maps, qualifies them with an LLM based on patient reviews, finds decision-maker contact emails through a six-step discovery waterfall, generates personalised cold emails, and delivers them via Resend while tracking delivery events through webhooks.

---

## Key Features

- **Geographic discovery** — Adaptive grid search across any city (bounding box from OpenStreetMap), subdividing dense areas automatically.
- **LLM qualification** — Groq-hosted Llama 3.3 70B scores each clinic (0–10) on phone and booking pain points, using verbatim complaint quotes from reviews.
- **Multi-step email discovery** — HTML scraping, NPI registry owner lookup, SMTP pattern verification, and optional Snov.io, SERP, browser-agent, and Hunter.io fallbacks. Free methods run first; paid services are a last resort.
- **Personalised outreach** — Cold emails generated from each lead's qualification data, sent through Resend with delivery tracking.
- **Scheduling and live progress** — Cron-based recurring scrape jobs and a Server-Sent Events stream for real-time job progress.
- **Secure by default** — JWT authentication, per-endpoint rate limiting, security headers, and per-user ownership checks.

---

## Architecture

```
app/
├── main.py                   # FastAPI app, lifespan hooks, router registration
├── config.py                 # Pydantic settings (all environment variables)
├── database.py               # Supabase client singleton
│
├── core/                     # Shared infrastructure
│   ├── security.py           # JWT (access + refresh), bcrypt
│   ├── middleware.py         # CORS, security headers
│   └── rate_limit.py         # SlowAPI limiter
│
├── auth/                     # Registration, login, token refresh
├── leads/                    # Lead listing, detail, and status updates
│
├── scraper/                  # Scrape pipeline
│   ├── router.py             # Scrape job endpoints (incl. SSE progress stream)
│   ├── pipeline.py           # Orchestrates Stage 1 → 2 → 3 and persists results
│   ├── stage1_scrape.py      # Google Maps scraping (Camoufox)
│   ├── stage2_categorize.py  # LLM qualification (Groq)
│   ├── stage3_emails.py      # Six-step email discovery waterfall
│   ├── grid_generator.py     # Adaptive geographic grid for area campaigns
│   ├── nominatim.py          # City bounding-box lookup (OpenStreetMap)
│   ├── hours_utils.py        # Opening-hours and after-hours gap analysis
│   └── maps_dom.py           # Google Maps DOM selectors and helpers
│
├── campaigns/                # Grid-based area scrape campaigns
│   ├── service.py            # Campaign creation, cell processing, subdivision
│   └── repository.py         # Campaign persistence and statistics
│
├── outreach/                 # Cold email generation and delivery
│   ├── router.py             # Outreach, email campaign, and Resend webhook endpoints
│   ├── campaign_service.py   # Bulk draft generation and send orchestration
│   ├── email_generator.py    # LLM-powered email drafting
│   └── sender.py             # Resend delivery, plain text → HTML
│
├── cron/                     # Scheduled scrape jobs (APScheduler)
│
└── niches/                   # Vertical configuration
    ├── base.py               # NicheConfig base class
    └── dental.py             # Dental personas, pain points, and prompts
```

**Patterns used:** feature-based modules, repository pattern, dependency injection (FastAPI `Depends`), singleton (database client, scheduler), pipeline (Stage 1 → 2 → 3), and factory (`NicheConfig` inheritance).

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI |
| Database | Supabase (PostgreSQL with row-level security) |
| Authentication | JWT (HS256) and bcrypt |
| Scraping | Camoufox (anti-detect browser), Playwright |
| LLM | Groq API (`llama-3.3-70b-versatile`) |
| Email delivery | Resend |
| Scheduling | APScheduler |
| Rate limiting | SlowAPI |
| Email discovery | NPPES NPI Registry, Snov.io, SerpAPI, Hunter.io (optional) |

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package and project manager)
- A [Supabase](https://supabase.com) project
- A [Groq](https://console.groq.com) API key (free tier available)
- A [Resend](https://resend.com) API key and a verified sending domain

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
cd ReceptAI
```

### 2. Install dependencies

`uv` creates the virtual environment and installs everything from `pyproject.toml`:

```bash
uv sync
```

To include development dependencies (pytest and related tools):

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

Open `.env` and fill in the values described in [Environment Variables](#environment-variables).

### 5. Set up the database

In your Supabase project, open **SQL Editor** and run the migrations in order:

```
supabase/migrations/001_initial_schema.sql
supabase/migrations/002_add_job_progress.sql
supabase/migrations/003_campaigns_grid.sql
supabase/migrations/004_hours_and_booking.sql
```

Together these create the application tables with row-level security policies, seed the `dental` niche, and add its email templates.

### 6. Run the server

```bash
uv run uvicorn app.main:app --reload
```

- API: `http://localhost:8000`
- Interactive docs: `http://localhost:8000/docs`

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | JWT signing secret (minimum 32 characters) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Access token lifetime (default: `120`) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | Refresh token lifetime (default: `7`) |
| `SUPABASE_URL` | Yes | Project URL from the Supabase dashboard |
| `SUPABASE_ANON_KEY` | Yes | Anon (public) key from Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Service role key used for database calls |
| `GROQ_API_KEY` | Yes | Groq API key for qualification and email generation |
| `RESEND_API_KEY` | Yes | Resend API key for email delivery |
| `FROM_EMAIL` | Yes | Verified sender address (e.g. `outreach@yourdomain.com`) |
| `FROM_NAME` | No | Sender display name (default: `ReceptAI Outreach`) |
| `DEMO_PHONE` | Recommended | Phone number inserted into cold email CTAs |
| `DEMO_URL` | Recommended | Demo URL inserted into cold email CTAs |
| `BOOKING_URL` | Recommended | Booking link inserted into cold email CTAs |
| `HUNTER_API_KEY` | No | Hunter.io key (Step 5 fallback in email discovery) |
| `SNOV_CLIENT_ID` | No | Snov.io client ID (Step 2 email discovery) |
| `SNOV_CLIENT_SECRET` | No | Snov.io client secret (Step 2 email discovery) |
| `SERPAPI_KEY` | No | SerpAPI key for Google SERP email mining (Step 3) |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default: `http://localhost:3000`) |

Optional discovery services are skipped automatically when their keys are not set.

---

## API Overview

All endpoints except `/auth/*`, `/health`, and `/outreach/webhook/resend` require a `Bearer` token in the `Authorization` header.

### Auth

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Create an account |
| POST | `/auth/token` | Log in and receive access and refresh tokens |
| POST | `/auth/refresh` | Exchange a refresh token for a new access token |

### Scraper

| Method | Path | Description |
|--------|------|-------------|
| POST | `/scraper/jobs` | Start a scrape job for a city or location |
| GET | `/scraper/jobs` | List recent jobs |
| GET | `/scraper/jobs/{job_id}` | Get job status and progress |
| GET | `/scraper/jobs/{job_id}/stream` | Live job progress via Server-Sent Events |

### Area Campaigns

Grid-based campaigns that cover an entire city by searching adaptively sized cells.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/campaigns` | Create a grid campaign for a city and country |
| GET | `/campaigns` | List your campaigns |
| GET | `/campaigns/{campaign_id}` | Get campaign details and statistics |
| POST | `/campaigns/{campaign_id}/pause` | Pause a running campaign |
| POST | `/campaigns/{campaign_id}/resume` | Resume a paused campaign |

### Leads

| Method | Path | Description |
|--------|------|-------------|
| GET | `/leads/` | List leads (filter by `niche` and `status`) |
| GET | `/leads/{lead_id}` | Get a lead with its clinic details |
| PATCH | `/leads/{lead_id}` | Update lead status |

### Outreach

| Method | Path | Description |
|--------|------|-------------|
| POST | `/outreach/generate` | Preview a generated cold email for a lead |
| POST | `/outreach/campaigns` | Create an email outreach campaign |
| GET | `/outreach/campaigns` | List email outreach campaigns |
| GET | `/outreach/campaigns/{campaign_id}/emails` | List emails in a campaign |
| POST | `/outreach/campaigns/{campaign_id}/generate` | Bulk-generate draft emails for leads |
| POST | `/outreach/campaigns/{campaign_id}/send` | Send all drafts via Resend |
| POST | `/outreach/webhook/resend` | Resend delivery event webhook (signature verified) |

### Cron Jobs

| Method | Path | Description |
|--------|------|-------------|
| POST | `/cron/jobs` | Create a scheduled scrape job |
| GET | `/cron/jobs` | List your cron jobs |
| GET | `/cron/jobs/{job_id}` | Get a single cron job |
| PATCH | `/cron/jobs/{job_id}/toggle` | Enable or disable a cron job |
| DELETE | `/cron/jobs/{job_id}` | Delete a cron job |

### Meta

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |

---

## Scrape Pipeline

A scrape job (triggered directly via `POST /scraper/jobs`, by a cron job, or per grid cell in an area campaign) runs three stages asynchronously:

```
Stage 1 — Google Maps scraping (Camoufox)
  ↓  clinic name, address, phone, website, reviews, opening hours
Stage 2 — LLM qualification (Groq)
  ↓  score 0–10, primary pain point, verbatim complaint quote, personalisation hook
Stage 3 — Email discovery (waterfall, stops at first hit)
  0    HTML scrape of the homepage and contact/about pages
  0.5  NPI Registry lookup for the clinic's authorised official (US only)
  1    SMTP verification of name-based and common address patterns
  2    Snov.io domain search (optional)
  3    Google SERP email mining via SerpAPI (optional)
  4    Browser agent as a last resort
  5    Hunter.io lookup (optional, paid)
```

Each stage writes its results to Supabase (`clinics` → `qualifications` → `leads`) as it completes, and job progress is available through the status and stream endpoints.

**Area campaigns** start from a city bounding box divided into circular cells. Any cell that returns a saturated number of results is subdivided, up to a configured maximum depth, so dense areas are covered without wasted requests.

---

## Running Tests

```bash
uv run pytest tests/ -v
```

The test suite mocks Supabase through fixtures in `tests/conftest.py`, so no live database connection is required.

To run a one-off Stage 1 scrape and write the results to JSON:

```bash
uv run python scripts/sample_scrape.py
```

---

## Adding a New Niche

1. Create `app/niches/<niche>.py` with a class that extends `NicheConfig`:

   ```python
   from app.niches.base import NicheConfig

   class MedSpaConfig(NicheConfig):
       niche_id = "medspa"
       search_term = "medical spas"
       pain_points = ["missed_calls", "no_online_booking"]
       # persona, prompts, and other niche-specific settings
   ```

2. Register the class in `NICHE_MAP` in `app/scraper/router.py`.
3. Insert the niche slug into the `niches` table in Supabase.

---

## Security

- All database access uses the Supabase service role key, which bypasses row-level security, so the API layer enforces ownership checks on every user-scoped resource.
- Rate limits apply per endpoint, for example 3 scrape jobs per minute and 10 login attempts per minute.
- `SecurityHeadersMiddleware` sets Content-Security-Policy, X-Frame-Options, and Permissions-Policy headers.
- Refresh tokens carry a `"type": "refresh"` claim so that access tokens cannot be used to refresh a session.
