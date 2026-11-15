"""Health check endpoint."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Report service health, DB connectivity, scheduler state, and data freshness."""
    db_status = "error"
    last_scored_at: str | None = None
    weather_data_until: str | None = None

    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health check DB ping failed: %s", exc)

    if db_status == "ok":
        try:
            res = await db.execute(text("SELECT MAX(computed_at) FROM risk_snapshots"))
            val = res.scalar()
            if val is not None:
                last_scored_at = val.isoformat() if hasattr(val, "isoformat") else str(val)
        except SQLAlchemyError:
            # Table may not exist yet before M1 migrations
            pass

        try:
            res = await db.execute(text("SELECT MAX(date) FROM weather_daily"))
            val = res.scalar()
            if val is not None:
                weather_data_until = val.isoformat() if hasattr(val, "isoformat") else str(val)
        except SQLAlchemyError:
            pass

    if not settings.scheduler_enabled:
        scheduler_status = "disabled"
    elif getattr(request.app.state, "scheduler_running", False):
        scheduler_status = "running"
    else:
        scheduler_status = "stopped"

    return {
        "status": "ok",
        "db": db_status,
        "scheduler": scheduler_status,
        "scoring_version": settings.scoring_version,
        "model": settings.openrouter_model,
        "last_scored_at": last_scored_at,
        "weather_data_until": weather_data_until,
    }
