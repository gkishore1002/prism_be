from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.bootstrap import bootstrap_if_empty, ensure_default_centers
from app.core.config import settings
from app.db.base import Base
from app.db.migrate import run_migrations
from app.db.session import SessionLocal, engine
from app.services.deployment import backfill_initialization_for_legacy
import app.models  # noqa: F401 — register models with Base

logger = logging.getLogger(__name__)

CSC_JOB_INTERVAL_SECONDS = 86_400


async def _csc_scheduler_loop() -> None:
    """Run CSC maintenance daily and once shortly after startup."""
    from app.services.csc_scheduler import run_daily_csc_job

    await asyncio.sleep(60)
    while True:
        db = SessionLocal()
        try:
            stats = run_daily_csc_job(db)
            db.commit()
            logger.info("CSC scheduler run completed: %s", stats)
        except Exception:
            logger.exception("CSC scheduler run failed")
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(CSC_JOB_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    db = SessionLocal()
    try:
        from app.services.seed import print_seed_credentials, seed_demo_platform

        if seed_demo_platform():
            print_seed_credentials()
        elif settings.seed_demo:
            logger.info("Demo seed already present (super user + demo org)")
        if settings.auto_bootstrap:
            bootstrap_if_empty(db)
        backfill_initialization_for_legacy(db)
        created = ensure_default_centers(db)
        if created:
            print(f"Created default HQ center for {created} institution(s) without centers.")
        from app.services.analytics_recompute import recompute_all_institutions

        updated = recompute_all_institutions(db)
        if updated:
            print(f"Recomputed analytics for {updated} student profile(s).")
    finally:
        db.close()

    scheduler_task = None
    if settings.csc_scheduler_enabled:
        scheduler_task = asyncio.create_task(_csc_scheduler_loop())
    try:
        yield
    finally:
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


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


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "Prism API",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "api": "/api/v1",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
