import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.routers import auth, inventory, reports
from app.seed import seed_if_empty
from app.services.sheets_sync import sync_mirror

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("supply-api")
scheduler = BackgroundScheduler()


def _scheduled_sync() -> None:
    db = SessionLocal()
    try:
        sync_mirror(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    if settings.seed_on_startup:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()

    if settings.google_sheet_sync_enabled:
        scheduler.add_job(
            _scheduled_sync,
            "interval",
            minutes=max(1, settings.sheet_sync_interval_minutes),
            id="sheet_sync",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Sheet sync scheduler started")

    yield

    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Supply Room Inventory API", version="1.0.0", lifespan=lifespan)
settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(inventory.router, prefix="/api")
app.include_router(reports.router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/sync/sheets")
def trigger_sheet_sync():
    db = SessionLocal()
    try:
        return sync_mirror(db)
    finally:
        db.close()
