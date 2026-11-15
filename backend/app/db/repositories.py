"""Database query helpers for hubs, snapshots, and daily weather."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub, RiskSnapshot, WeatherDaily


async def list_hubs_with_latest_snapshot(
    session: AsyncSession,
    *,
    region: str | None = None,
) -> list[tuple[Hub, RiskSnapshot | None]]:
    """Return all hubs (optionally filtered by region) paired with their latest RiskSnapshot."""
    latest_subq = (
        select(
            RiskSnapshot.hub_id,
            func.max(RiskSnapshot.computed_at).label("max_computed_at"),
        )
        .group_by(RiskSnapshot.hub_id)
        .subquery()
    )

    latest_snap_subq = (
        select(RiskSnapshot)
        .join(
            latest_subq,
            and_(
                RiskSnapshot.hub_id == latest_subq.c.hub_id,
                RiskSnapshot.computed_at == latest_subq.c.max_computed_at,
            ),
        )
        .subquery()
    )

    stmt = select(Hub).order_by(Hub.code)
    if region is not None:
        stmt = stmt.where(Hub.region == region)
    hubs = (await session.execute(stmt)).scalars().all()

    if not hubs:
        return []

    hub_ids = [h.id for h in hubs]
    snap_stmt = (
        select(RiskSnapshot)
        .where(
            RiskSnapshot.hub_id.in_(hub_ids),
            RiskSnapshot.id.in_(select(latest_snap_subq.c.id)),
        )
        .order_by(RiskSnapshot.id.desc())
    )
    snaps = (await session.execute(snap_stmt)).scalars().all()

    snap_by_hub: dict[int, RiskSnapshot] = {}
    for snap in snaps:
        if snap.hub_id not in snap_by_hub:
            snap_by_hub[snap.hub_id] = snap

    return [(hub, snap_by_hub.get(hub.id)) for hub in hubs]


async def get_hub_by_code(session: AsyncSession, code: str) -> Hub | None:
    """Look up a single hub by code (case-insensitive)."""
    normalized = code.strip().upper()
    return await session.scalar(select(Hub).where(Hub.code == normalized))


async def get_latest_snapshot_for_hub(
    session: AsyncSession,
    hub_id: int,
) -> RiskSnapshot | None:
    """Fetch the most recent RiskSnapshot for a given hub ID."""
    stmt = (
        select(RiskSnapshot)
        .where(RiskSnapshot.hub_id == hub_id)
        .order_by(desc(RiskSnapshot.computed_at), desc(RiskSnapshot.id))
        .limit(1)
    )
    return await session.scalar(stmt)


async def get_hub_snapshot_history(
    session: AsyncSession,
    hub_id: int,
    *,
    limit: int = 30,
) -> Sequence[RiskSnapshot]:
    """Fetch recent RiskSnapshots for a hub, newest first."""
    stmt = (
        select(RiskSnapshot)
        .where(RiskSnapshot.hub_id == hub_id)
        .order_by(desc(RiskSnapshot.computed_at), desc(RiskSnapshot.id))
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_weather_range(
    session: AsyncSession,
    hub_id: int,
    start_date: date,
    end_date: date,
) -> Sequence[WeatherDaily]:
    """Fetch WeatherDaily rows for a hub within `[start_date, end_date]` inclusive."""
    stmt = (
        select(WeatherDaily)
        .where(
            WeatherDaily.hub_id == hub_id,
            WeatherDaily.date >= start_date,
            WeatherDaily.date <= end_date,
        )
        .order_by(WeatherDaily.date)
    )
    return (await session.execute(stmt)).scalars().all()
