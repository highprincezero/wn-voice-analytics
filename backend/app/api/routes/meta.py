from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/meta")
def meta() -> dict:
    settings = get_settings()
    return {
        "llm_provider": settings.llm_provider,
        "safety_provider": settings.safety_provider,
        "blob_provider": settings.blob_provider,
        "analysis_mode": settings.analysis_mode,
        "broker": settings.broker,
        "home_region": settings.home_region,
    }
