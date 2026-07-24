from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.bootstrap import bootstrap_if_empty, ensure_default_centers
from app.core.config import settings
from app.db.base import Base
from app.db.migrate import run_migrations
from app.db.session import SessionLocal, engine
import app.models  # noqa: F401 — register models with Base


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    db = SessionLocal()
    try:
        bootstrap_if_empty(db)
        created = ensure_default_centers(db)
        if created:
            print(f"Created default HQ center for {created} institution(s) without centers.")
        from app.services.analytics_recompute import recompute_all_institutions

        updated = recompute_all_institutions(db)
        if updated:
            print(f"Recomputed analytics for {updated} student profile(s).")
    finally:
        db.close()
    yield


app = FastAPI(
    title="Prism API",
    description="CRUD backend for the Prism academic intelligence platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
