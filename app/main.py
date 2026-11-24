from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.base import SessionLocal, engine
from app.db.migrate import prepare_database
from app.routers import account, admin, runs, scoring, workspace


@asynccontextmanager
async def lifespan(app: FastAPI):
    prepare_database(engine)          # PostgreSQL: Alembic migrations · SQLite (dev): automatic upgrade
    if settings.seed_demo_data:
        from app.seed import seed, seed_reference
        with SessionLocal() as db:
            seed(db)
            seed_reference(db)        # idempotent: reference rows added by later UI versions
            if settings.seed_sample_data:
                from app.seed_samples import seed_samples
                seed_samples(db)      # idempotent: sample activity for frontend testing
    yield


app = FastAPI(title="Inflammation - New Indication Platform API", version="0.4.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

API = "/api/v1"
for r in (account.router, runs.router, workspace.router, scoring.router, admin.router):
    app.include_router(r, prefix=API)


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


@app.get("/health/dependencies", tags=["ops"])
def health_dependencies():
    """Status of external services this API relies on (for monitoring and the Data Connectors page)."""
    from app.services import scientific_search
    ai = scientific_search.health() if settings.ai_search_base_url else {"configured": False, "ok": None,
                                                                        "detail": "AI_SEARCH_BASE_URL is not set: research runs fail and AI Rephrase returns 503"}
    return {"ai_scientific_search": ai}
