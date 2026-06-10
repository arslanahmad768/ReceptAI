from fastapi import FastAPI
from app.core.middleware import register_middleware
from app.auth.router import router as auth_router
from app.scraper.router import router as scraper_router
from app.leads.router import router as leads_router

app = FastAPI(title="ReceptAI API", version="0.1.0")
register_middleware(app)
app.include_router(auth_router)
app.include_router(scraper_router)
app.include_router(leads_router)

@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
