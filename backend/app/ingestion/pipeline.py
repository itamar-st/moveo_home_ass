"""Ingestion pipeline orchestration for weather history and NWS alerts."""

import logging
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub
from app.ingestion.nws import fetch_active_alerts
from app.ingestion.open_meteo import fetch_archive_daily, upsert_weather_daily

logger = logging.getLogger(__name__)


def compute_coverage_pct(
    rows: Sequence[Mapping[str, Any]],
    start_date: date,
    end_date: date,
) -> float:
    """Compute percentage of expected days with non-null weather observations."""
    expected_days = max((end_date - start_date).days + 1, 1)
    valid_days = sum(
        1
        for row in rows
        if row.get("temp_max_c") is not None
        or row.get("temp_min_c") is not None
        or row.get("precip_mm") is not None
    )
    return round(min((valid_days / expected_days) * 100.0, 100.0), 2)


async def refresh_weather_all_hubs(
    session: AsyncSession,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    days: int = 7,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch Open-Meteo archive daily data for all hubs and upsert into `weather_daily`."""
    resolved_end = end_date or (date.today() - timedelta(days=1))
    resolved_start = start_date or (resolved_end - timedelta(days=max(days - 1, 0)))

    result = await session.execute(select(Hub).order_by(Hub.code))
    hubs = result.scalars().all()

    total_upserted = 0
    hub_outcomes: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    async def _run(http_client: httpx.AsyncClient) -> None:
        nonlocal total_upserted
        for hub in hubs:
            try:
                rows = await fetch_archive_daily(
                    lat=hub.latitude,
                    lon=hub.longitude,
                    timezone=hub.timezone,
                    start_date=resolved_start,
                    end_date=resolved_end,
                    client=http_client,
                )
                upserted = await upsert_weather_daily(session, hub.id, rows, commit=True)
                coverage_pct = compute_coverage_pct(rows, resolved_start, resolved_end)
                total_upserted += upserted
                outcome = {
                    "hub_code": hub.code,
                    "rows_upserted": upserted,
                    "coverage_pct": coverage_pct,
                }
                hub_outcomes.append(outcome)
                logger.info(
                    "weather_ingested hub_code=%s rows_upserted=%d coverage_pct=%.2f window=%s..%s",
                    hub.code,
                    upserted,
                    coverage_pct,
                    resolved_start.isoformat(),
                    resolved_end.isoformat(),
                )
            except Exception as exc:
                logger.exception("weather_ingestion_failed hub_code=%s", hub.code)
                errors.append({"hub_code": hub.code, "error": str(exc)})

    if client is not None:
        await _run(client)
    else:
        async with httpx.AsyncClient(timeout=30.0) as owned_client:
            await _run(owned_client)

    summary = {
        "hub_count": len(hubs),
        "rows_upserted": total_upserted,
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "hubs": hub_outcomes,
        "errors": errors,
    }
    logger.info(
        "weather_refresh_complete hub_count=%d rows_upserted=%d errors=%d",
        summary["hub_count"],
        summary["rows_upserted"],
        len(errors),
    )
    return summary


async def fetch_alerts_all_hubs(
    session: AsyncSession,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch active NWS alerts for all hubs (in-memory, not persisted to a table)."""
    result = await session.execute(select(Hub).order_by(Hub.code))
    hubs = result.scalars().all()

    alerts_by_hub: dict[str, list[dict[str, Any]]] = {}
    alerts_found = 0
    errors: list[dict[str, str]] = []

    async def _run(http_client: httpx.AsyncClient) -> None:
        nonlocal alerts_found
        for hub in hubs:
            try:
                alerts = await fetch_active_alerts(
                    lat=hub.latitude,
                    lon=hub.longitude,
                    client=http_client,
                )
                alerts_by_hub[hub.code] = alerts
                alerts_found += len(alerts)
            except Exception as exc:
                logger.exception("nws_hub_failed hub_code=%s", hub.code)
                alerts_by_hub[hub.code] = []
                errors.append({"hub_code": hub.code, "error": str(exc)})

    if client is not None:
        await _run(client)
    else:
        async with httpx.AsyncClient(timeout=15.0) as owned_client:
            await _run(owned_client)

    logger.info(
        "nws_refresh_complete hub_count=%d alerts_found=%d errors=%d",
        len(hubs),
        alerts_found,
        len(errors),
    )
    return {
        "hub_count": len(hubs),
        "alerts_found": alerts_found,
        "alerts_by_hub": alerts_by_hub,
        "errors": errors,
    }
