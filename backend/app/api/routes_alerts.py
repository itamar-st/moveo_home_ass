"""REST and SSE endpoints for risk alerts (§8.1, §8.3)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.alerts.broadcaster import alert_broadcaster
from app.api.deps import get_db
from app.db.models import Hub, RiskAlert
from app.schemas.api import RiskAlertOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[RiskAlertOut])
async def list_alerts(
    db: Annotated[AsyncSession, Depends(get_db)],
    hub: Annotated[str | None, Query(description="Filter by hub code (e.g. MIA)")] = None,
    since: Annotated[
        datetime | None,
        Query(description="Return alerts created at or after this ISO-8601 timestamp"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RiskAlertOut]:
    """List persisted risk alerts, newest first."""
    stmt = (
        select(RiskAlert, Hub.code)
        .join(Hub, RiskAlert.hub_id == Hub.id)
        .order_by(desc(RiskAlert.created_at), desc(RiskAlert.id))
        .limit(limit)
    )
    if hub:
        stmt = stmt.where(Hub.code == hub.strip().upper())
    if since is not None:
        normalized_since = (
            since.astimezone(UTC).replace(tzinfo=None) if since.tzinfo is not None else since
        )
        stmt = stmt.where(RiskAlert.created_at >= normalized_since)

    rows = (await db.execute(stmt)).all()
    return [
        RiskAlertOut(
            id=alert.id,
            hub=hub_code,
            alert_type=alert.alert_type,
            old_score=float(alert.old_score) if alert.old_score is not None else None,
            new_score=float(alert.new_score),
            message=alert.message,
            created_at=alert.created_at,
        )
        for alert, hub_code in rows
    ]


@router.get("/stream")
async def stream_alerts(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EventSourceResponse:
    """Stream live `risk_alert` and `scoring_run` events over SSE (§8.3)."""
    latest_id = await db.scalar(select(func.max(RiskAlert.id)))
    bus = getattr(request.app.state, "alert_broadcaster", None) or alert_broadcaster

    async def event_generator() -> AsyncIterator[ServerSentEvent]:
        queue = bus.subscribe()
        try:
            yield ServerSentEvent(
                event="hello",
                data=json.dumps(
                    {
                        "server_time": datetime.now(UTC).isoformat(),
                        "latest_alert_id": int(latest_id) if latest_id is not None else None,
                    }
                ),
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                yield ServerSentEvent(
                    event=msg["event"],
                    data=json.dumps(msg["data"], default=str),
                )
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(
        event_generator(),
        ping=15,
        ping_message_factory=lambda: ServerSentEvent(comment="ping"),
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
