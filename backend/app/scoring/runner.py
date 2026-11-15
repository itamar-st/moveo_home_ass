"""Async scoring runner: loads DB weather/NRI, fetches live NWS alerts, persists snapshots."""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub, RiskSnapshot, WeatherDaily
from app.ingestion.nws import fetch_active_alerts_with_status
from app.scoring.config import DEFAULT_SCORING_CONFIG, ScoringConfig
from app.scoring.engine import score_hub
from app.scoring.metrics import climatology_window

logger = logging.getLogger(__name__)


async def _load_latest_snapshots_map(session: AsyncSession) -> dict[int, RiskSnapshot]:
    """Return the most recent RiskSnapshot keyed by `hub_id`."""
    latest_time_subq = (
        select(
            RiskSnapshot.hub_id,
            func.max(RiskSnapshot.computed_at).label("max_computed_at"),
        )
        .group_by(RiskSnapshot.hub_id)
        .subquery()
    )
    stmt = select(RiskSnapshot).join(
        latest_time_subq,
        and_(
            RiskSnapshot.hub_id == latest_time_subq.c.hub_id,
            RiskSnapshot.computed_at == latest_time_subq.c.max_computed_at,
        ),
    )
    rows = (await session.execute(stmt)).scalars().all()
    # If multiple rows share max_computed_at for a hub, keep highest id
    result: dict[int, RiskSnapshot] = {}
    for snap in rows:
        prev = result.get(snap.hub_id)
        if prev is None or snap.id > prev.id:
            result[snap.hub_id] = snap
    return result


async def rescore_all_hubs(
    session: AsyncSession,
    *,
    fetch_nws: bool = True,
    simulated_alerts_by_hub: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    computed_at: datetime | None = None,
    reference_date: date | None = None,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Score all seeded hubs deterministically and persist a new `risk_snapshots` batch."""
    run_ts = (computed_at or datetime.now(UTC)).replace(tzinfo=None, microsecond=0)
    start_date, end_date = climatology_window(reference_date, years=config.climatology_years)

    hubs = (await session.execute(select(Hub).order_by(Hub.code))).scalars().all()
    if not hubs:
        logger.warning("rescore_skipped reason=no_hubs_in_db")
        return {
            "computed_at": run_ts.isoformat(),
            "hubs_scored": 0,
            "nws_failures": 0,
            "snapshots": [],
            "previous_snapshots": {},
            "hubs_by_id": {},
        }

    previous_snapshots = await _load_latest_snapshots_map(session)

    # Load all weather_daily rows in the climatology window in one query
    weather_stmt = (
        select(WeatherDaily)
        .where(WeatherDaily.date >= start_date, WeatherDaily.date <= end_date)
        .order_by(WeatherDaily.hub_id, WeatherDaily.date)
    )
    weather_rows = (await session.execute(weather_stmt)).scalars().all()
    rows_by_hub: dict[int, list[WeatherDaily]] = {}
    for row in weather_rows:
        rows_by_hub.setdefault(row.hub_id, []).append(row)

    sim_map = simulated_alerts_by_hub or {}
    created_snapshots: list[RiskSnapshot] = []
    nws_failures = 0

    async def _score_with_client(http_client: httpx.AsyncClient | None) -> None:
        nonlocal nws_failures
        for hub in hubs:
            prev_snap = previous_snapshots.get(hub.id)
            extra_caveats: list[str] = []
            live_alerts: list[dict[str, Any]] = []

            if fetch_nws:
                fetched, ok = await fetch_active_alerts_with_status(
                    hub.latitude,
                    hub.longitude,
                    client=http_client,
                )
                if ok:
                    live_alerts = fetched
                else:
                    nws_failures += 1
                    extra_caveats.append(
                        "NWS live alert fetch failed; retained previous alert state."
                    )
                    if prev_snap is not None:
                        prev_alerts = (prev_snap.details or {}).get("active_alerts") or []
                        live_alerts = [
                            dict(a) for a in prev_alerts if not a.get("simulated")
                        ]
            elif prev_snap is not None:
                prev_alerts = (prev_snap.details or {}).get("active_alerts") or []
                live_alerts = [dict(a) for a in prev_alerts if not a.get("simulated")]

            sim_alerts = [dict(a) for a in sim_map.get(hub.code, ())]
            combined_alerts = [*live_alerts, *sim_alerts]

            hub_rows = rows_by_hub.get(hub.id, [])
            score_res = score_hub(
                hub_rows,
                hub.nri,
                combined_alerts,
                start_date=start_date,
                end_date=end_date,
                config=config,
                extra_caveats=extra_caveats,
            )

            snapshot = RiskSnapshot(
                hub_id=hub.id,
                computed_at=run_ts,
                overall_score=Decimal(f"{score_res.overall_score:.2f}"),
                operational_score=Decimal(f"{score_res.operational_score:.2f}"),
                details=score_res.details,
            )
            session.add(snapshot)
            created_snapshots.append(snapshot)

    if fetch_nws and client is None:
        async with httpx.AsyncClient(timeout=15.0) as owned_client:
            await _score_with_client(owned_client)
    else:
        await _score_with_client(client)

    await session.commit()
    for snap in created_snapshots:
        await session.refresh(snap)

    logger.info(
        "rescore_completed computed_at=%s hubs_scored=%d nws_failures=%d window=%s..%s",
        run_ts.isoformat(),
        len(created_snapshots),
        nws_failures,
        start_date.isoformat(),
        end_date.isoformat(),
    )

    return {
        "computed_at": run_ts.isoformat(),
        "hubs_scored": len(created_snapshots),
        "nws_failures": nws_failures,
        "window": [start_date.isoformat(), end_date.isoformat()],
        "snapshots": created_snapshots,
        "previous_snapshots": previous_snapshots,
        "hubs_by_id": {hub.id: hub for hub in hubs},
    }
