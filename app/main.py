from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.middleware import register_middleware
from app.auth.router import router as auth_router
from app.scraper.router import router as scraper_router
from app.leads.router import router as leads_router
from app.outreach.router import router as outreach_router
from app.cron.router import router as cron_router
from app.campaigns.router import router as campaigns_router
from app.database import get_db
from app.cron.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    # DB check first: if Supabase is unreachable, there is no point starting the scheduler
    try:
        get_db().table("niches").select("id").limit(1).execute()
    except Exception as e:
        raise RuntimeError(f"FATAL: Supabase unreachable — {e}") from e
    start_scheduler()
    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    stop_scheduler()


app = FastAPI(
    title="ReceptAI API",
    version="1.0.0",
    description=(
        "Full-stack lead engine: scrape → qualify → find emails → "
        "generate personalised cold emails → send via Resend → track replies."
    ),
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

register_middleware(app)
app.include_router(auth_router)
app.include_router(scraper_router)
app.include_router(leads_router)
app.include_router(outreach_router)
app.include_router(cron_router)
app.include_router(campaigns_router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": "1.0.0"}
