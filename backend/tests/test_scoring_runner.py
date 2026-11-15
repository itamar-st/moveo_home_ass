"""Integration tests for the async scoring runner (`app/scoring/runner.py`)."""

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Hub, RiskSnapshot, WeatherDaily
from app.ingestion.nws import NWS_ALERTS_URL
from app.scoring.runner import rescore_all_hubs
from scripts.seed import seed_hubs


@pytest.fixture
async def scoring_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


async def _populate_synthetic_climatology(
    session: AsyncSession,
    *,
    start: date = date(2021, 1, 1),
    end: date = date(2025, 12, 31),
) -> dict[str, Hub]:
    """Seed all 20 hubs and insert realistic synthetic daily profiles for key test hubs."""
    await seed_hubs(session)
    hubs = (await session.execute(select(Hub).order_by(Hub.code))).scalars().all()
    hub_by_code = {h.code: h for h in hubs}

    # Representative annual patterns across 5 years (1826 days)
    profiles: dict[str, dict[str, Any]] = {
        "MSP": {"snow_days": 55, "freeze_days": 220, "precip_days": 25, "heat_days": 0, "wind_days": 2},
        "ORD": {"snow_days": 40, "freeze_days": 115, "precip_days": 30, "heat_days": 1, "wind_days": 7},
        "DTW": {"snow_days": 38, "freeze_days": 90, "precip_days": 25, "heat_days": 1, "wind_days": 8},
        "MCI": {"snow_days": 22, "freeze_days": 80, "precip_days": 55, "heat_days": 8, "wind_days": 7},
        "IND": {"snow_days": 20, "freeze_days": 70, "precip_days": 30, "heat_days": 0, "wind_days": 5},
        "STL": {"snow_days": 16, "freeze_days": 60, "precip_days": 45, "heat_days": 5, "wind_days": 4},
        "MIA": {"snow_days": 0, "freeze_days": 0, "precip_days": 46, "heat_days": 0, "wind_days": 3},
        "MSY": {"snow_days": 0, "freeze_days": 0, "precip_days": 82, "heat_days": 6, "wind_days": 11},
        "IAH": {"snow_days": 0, "freeze_days": 1, "precip_days": 115, "heat_days": 30, "wind_days": 4},
        "DFW": {"snow_days": 4, "freeze_days": 10, "precip_days": 81, "heat_days": 104, "wind_days": 4},
        "PHX": {"snow_days": 0, "freeze_days": 0, "precip_days": 15, "heat_days": 500, "wind_days": 1},
    }

    total_days = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(total_days)]

    batch: list[WeatherDaily] = []
    for code, hub in hub_by_code.items():
        prof = profiles.get(
            code,
            {"snow_days": 5, "freeze_days": 10, "precip_days": 20, "heat_days": 2, "wind_days": 2},
        )
        for idx, d in enumerate(dates):
            batch.append(
                WeatherDaily(
                    hub_id=hub.id,
                    date=d,
                    temp_max_c=40.0 if idx < prof["heat_days"] else 20.0,
                    temp_min_c=-15.0 if idx < prof["freeze_days"] else 8.0,
                    precip_mm=30.0 if idx < prof["precip_days"] else 1.0,
                    snowfall_cm=4.0 if idx < prof["snow_days"] else 0.0,
                    gust_max_kmh=85.0 if idx < prof["wind_days"] else 25.0,
                    weather_code=73 if idx < prof["snow_days"] else 0,
                )
            )
    session.add_all(batch)
    await session.commit()
    return hub_by_code


async def test_rescore_all_hubs_empty_db(scoring_session: AsyncSession) -> None:
    result = await rescore_all_hubs(scoring_session, fetch_nws=False)
    assert result["hubs_scored"] == 0
    assert result["snapshots"] == []


@respx.mock
async def test_rescore_all_hubs_persists_shared_timestamp_and_sanity_checks(
    scoring_session: AsyncSession,
) -> None:
    hub_by_code = await _populate_synthetic_climatology(scoring_session)

    # Mock NWS: MIA has an Extreme Hurricane Warning; all other hubs have no alerts
    def _nws_responder(request: httpx.Request) -> httpx.Response:
        point = request.url.params.get("point", "")
        if point.startswith("25.7617"):
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {
                                "event": "Hurricane Warning",
                                "severity": "Extreme",
                                "headline": "Hurricane Warning issued for Miami-Dade",
                                "expires": "2026-11-16T12:00:00Z",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"features": []})

    respx.get(NWS_ALERTS_URL).mock(side_effect=_nws_responder)

    ts = datetime(2026, 11, 15, 18, 0, 0, tzinfo=UTC)
    summary = await rescore_all_hubs(
        scoring_session,
        fetch_nws=True,
        computed_at=ts,
        reference_date=date(2026, 11, 15),
    )

    assert summary["hubs_scored"] == 20
    assert summary["nws_failures"] == 0
    assert summary["window"] == ["2021-01-01", "2025-12-31"]

    snaps = (await scoring_session.execute(select(RiskSnapshot))).scalars().all()
    assert len(snaps) == 20
    assert len({s.computed_at for s in snaps}) == 1
    assert snaps[0].computed_at == datetime(2026, 11, 15, 18, 0, 0)

    snap_by_hub_id = {s.hub_id: s for s in snaps}

    # Check MIA operational bump (+30 from Extreme Hurricane Warning)
    mia_snap = snap_by_hub_id[hub_by_code["MIA"].id]
    assert mia_snap.details["live_bump"] == 30.0
    assert float(mia_snap.operational_score) == round(
        min(100.0, float(mia_snap.overall_score) + 30.0), 2
    )
    assert mia_snap.details["active_alerts"][0]["event"] == "Hurricane Warning"

    # Sanity check 1: Midwest winter top-3 should be MSP, ORD, DTW
    midwest_codes = ["ORD", "MSP", "DTW", "IND", "MCI", "STL"]
    midwest_winter_ranked = sorted(
        midwest_codes,
        key=lambda c: snap_by_hub_id[hub_by_code[c].id].details["hazards"]["winter"]["score"],
        reverse=True,
    )
    assert midwest_winter_ranked[:3] == ["MSP", "ORD", "DTW"]

    # Sanity check 2: Hurricane top-3 should be MSY, IAH, MIA
    hurricane_ranked = sorted(
        hub_by_code.keys(),
        key=lambda c: snap_by_hub_id[hub_by_code[c].id].details["hazards"]["hurricane"]["score"],
        reverse=True,
    )
    assert set(hurricane_ranked[:3]) == {"MSY", "IAH", "MIA"}

    # Sanity check 3: DFW drivers include severe_storm, heat, and ice_storm NRI
    dfw_drivers_text = " | ".join(snap_by_hub_id[hub_by_code["DFW"].id].details["drivers"])
    assert "severe_storm" in dfw_drivers_text
    assert "heat" in dfw_drivers_text
    assert "ice_storm NRI" in dfw_drivers_text


@respx.mock
async def test_rescore_nws_failure_retains_previous_alerts_with_caveat(
    scoring_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When NWS fails after retries, scoring completes and keeps previous live alerts."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    hub_by_code = await _populate_synthetic_climatology(scoring_session)

    # First run: MIA gets a Severe alert
    respx.get(NWS_ALERTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "event": "Tornado Warning",
                            "severity": "Severe",
                            "headline": "Tornado Warning",
                            "expires": "2026-11-16T00:00:00Z",
                        }
                    }
                ]
            },
        )
    )
    await rescore_all_hubs(
        scoring_session,
        fetch_nws=True,
        computed_at=datetime(2026, 11, 15, 17, 0, 0),
        reference_date=date(2026, 11, 15),
    )

    # Second run: NWS returns 503
    respx.get(NWS_ALERTS_URL).mock(return_value=httpx.Response(503, text="Upstream down"))
    summary2 = await rescore_all_hubs(
        scoring_session,
        fetch_nws=True,
        computed_at=datetime(2026, 11, 15, 18, 0, 0),
        reference_date=date(2026, 11, 15),
    )

    assert summary2["hubs_scored"] == 20
    assert summary2["nws_failures"] == 20

    mia_latest = await scoring_session.scalar(
        select(RiskSnapshot)
        .where(RiskSnapshot.hub_id == hub_by_code["MIA"].id)
        .order_by(RiskSnapshot.computed_at.desc())
        .limit(1)
    )
    assert mia_latest is not None
    assert mia_latest.details["live_bump"] == 20.0
    assert mia_latest.details["active_alerts"][0]["event"] == "Tornado Warning"
    assert any("NWS live alert fetch failed" in c for c in mia_latest.details["caveats"])

    total_snaps = await scoring_session.scalar(select(func.count()).select_from(RiskSnapshot))
    assert total_snaps == 40


async def test_rescore_merges_simulated_alerts_without_nws_network(
    scoring_session: AsyncSession,
) -> None:
    hub_by_code = await _populate_synthetic_climatology(scoring_session)

    summary = await rescore_all_hubs(
        scoring_session,
        fetch_nws=False,
        simulated_alerts_by_hub={
            "DEN": [
                {
                    "event": "Blizzard Warning",
                    "severity": "Extreme",
                    "headline": "Simulated Blizzard Warning",
                    "expires": "2026-11-16T06:00:00Z",
                    "simulated": True,
                }
            ]
        },
        computed_at=datetime(2026, 11, 15, 19, 0, 0),
        reference_date=date(2026, 11, 15),
    )
    assert summary["hubs_scored"] == 20

    den_snap = await scoring_session.scalar(
        select(RiskSnapshot).where(RiskSnapshot.hub_id == hub_by_code["DEN"].id)
    )
    assert den_snap is not None
    assert den_snap.details["live_bump"] == 30.0
    assert float(den_snap.operational_score) == round(
        min(100.0, float(den_snap.overall_score) + 30.0), 2
    )
