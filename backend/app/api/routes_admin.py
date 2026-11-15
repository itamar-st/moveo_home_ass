"""Admin webhook-trigger endpoints for rescoring and simulated alert demos (§8.1)."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.broadcaster import alert_broadcaster
from app.alerts.service import run_rescore_and_alerts
from app.alerts.simulated import add_simulated_alert
from app.api.deps import APIError, get_db, require_admin_token
from app.db.repositories import get_hub_by_code
from app.db.session import get_session_maker
from app.schemas.api import (
    AdminAcceptedResponse,
    AdminRescoreRequest,
    AdminSimulateAlertRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


def get_admin_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the async session maker used by background admin tasks."""
    return get_session_maker()


def _track_background_task(request: Request, coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    tasks: set[asyncio.Task] | None = getattr(request.app.state, "background_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state.background_tasks = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def _execute_rescore_job(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    refresh_nws: bool,
    refresh_weather: bool,
    broadcaster,
) -> None:
    try:
        async with session_maker() as session:
            await run_rescore_and_alerts(
                session,
                refresh_nws=refresh_nws,
                refresh_weather=refresh_weather,
                broadcaster=broadcaster,
            )
    except Exception:
        logger.exception("admin_rescore_job_failed")


@router.post(
    "/rescore",
    response_model=AdminAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_rescore(
    body: AdminRescoreRequest,
    request: Request,
    session_maker: Annotated[async_sessionmaker[AsyncSession], Depends(get_admin_session_maker)],
) -> AdminAcceptedResponse:
    """Webhook trigger to rescore all hubs (optionally refreshing NWS/weather first)."""
    bus = getattr(request.app.state, "alert_broadcaster", None) or alert_broadcaster
    _track_background_task(
        request,
        _execute_rescore_job(
            session_maker,
            refresh_nws=body.refresh_nws,
            refresh_weather=body.refresh_weather,
            broadcaster=bus,
        ),
    )
    return AdminAcceptedResponse()


@router.post(
    "/simulate-alert",
    response_model=AdminAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def simulate_alert(
    body: AdminSimulateAlertRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    session_maker: Annotated[async_sessionmaker[AsyncSession], Depends(get_admin_session_maker)],
) -> AdminAcceptedResponse:
    """Add an in-memory simulated NWS alert and trigger an immediate rescore."""
    hub = await get_hub_by_code(db, body.hub)
    if hub is None:
        raise APIError(
            404,
            "hub_not_found",
            f"Unknown hub code '{body.hub.upper()}'.",
            details={"hub": body.hub.upper()},
        )

    add_simulated_alert(
        hub.code,
        body.severity,
        body.event,
        duration_hours=body.duration_hours,
    )

    bus = getattr(request.app.state, "alert_broadcaster", None) or alert_broadcaster
    _track_background_task(
        request,
        _execute_rescore_job(
            session_maker,
            refresh_nws=False,
            refresh_weather=False,
            broadcaster=bus,
        ),
    )
    return AdminAcceptedResponse()
