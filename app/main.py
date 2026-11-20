from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.base import Base, SessionLocal, engine
from app.routers import account, admin, runs, scoring, workspace


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # use Alembic migrations in production instead
    if settings.seed_demo_data:
        from app.seed import seed
        with SessionLocal() as db:
            seed(db)
    yield


app = FastAPI(title="Inflammation - New Indication Platform API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

API = "/api/v1"
for r in (account.router, runs.router, workspace.router, scoring.router, admin.router):
    app.include_router(r, prefix=API)


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}
