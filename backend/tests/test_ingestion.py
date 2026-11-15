"""Tests for M2 data ingestion clients, pipeline, and scripts."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base, Hub, WeatherDaily
from app.ingestion.fema_nri import apply_nri_to_hubs, load_nri_data
from app.ingestion.nws import NWS_ALERTS_URL, fetch_active_alerts
from app.ingestion.open_meteo import (
    OPEN_METEO_ARCHIVE_URL,
    fetch_archive_daily,
    upsert_weather_daily,
)
from app.ingestion.pipeline import (
    compute_coverage_pct,
    fetch_alerts_all_hubs,
    refresh_weather_all_hubs,
)
from scripts.backfill_weather import compute_backfill_window
from scripts.fetch_nri import build_nri_hubs_map, transform_nri_feature
from scripts.seed import seed_hubs


async def _init_schema(session: AsyncSession) -> None:
    conn = await session.connection()
    await conn.run_sync(Base.metadata.create_all)


def _sample_open_meteo_payload() -> dict:
    return {
        "latitude": 39.7392,
        "longitude": -104.9903,
        "daily": {
            "time": ["2025-01-01", "2025-01-02"],
            "temperature_2m_max": [4.56, -1.2],
            "temperature_2m_min": [-6.78, None],
            "precipitation_sum": [0.0, 12.345],
            "snowfall_sum": [0.0, 5.5],
            "wind_gusts_10m_max": [28.4, 55.1],
            "weather_code": [0, 73],
        },
    }


@respx.mock
async def test_fetch_archive_daily_happy_path() -> None:
    """Open-Meteo archive response is parsed into Decimal/date WeatherDaily rows."""
    route = respx.get(OPEN_METEO_ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json=_sample_open_meteo_payload())
    )

    rows = await fetch_archive_daily(
        lat=39.7392,
        lon=-104.9903,
        timezone="America/Denver",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2),
    )

    assert route.called
    request_url = str(route.calls[0].request.url)
    assert "timezone=America%2FDenver" in request_url
    assert "temperature_2m_max" in request_url
    assert "snowfall_sum" in request_url

    assert len(rows) == 2
    assert rows[0] == {
        "date": date(2025, 1, 1),
        "temp_max_c": Decimal("4.6"),
        "temp_min_c": Decimal("-6.8"),
        "precip_mm": Decimal("0.00"),
        "snowfall_cm": Decimal("0.00"),
        "gust_max_kmh": Decimal("28.4"),
        "weather_code": 0,
    }
    assert rows[1]["temp_min_c"] is None
    assert rows[1]["precip_mm"] == Decimal("12.35")
    assert rows[1]["weather_code"] == 73


@respx.mock
async def test_fetch_archive_daily_retries_on_429_and_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retryable 429 and 503 responses are retried and eventually succeed."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    route = respx.get(OPEN_METEO_ARCHIVE_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": True}),
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(200, json=_sample_open_meteo_payload()),
        ]
    )

    rows = await fetch_archive_daily(
        lat=39.7392,
        lon=-104.9903,
        timezone="America/Denver",
        start_date="2025-01-01",
        end_date="2025-01-02",
    )

    assert route.call_count == 3
    assert len(rows) == 2


@respx.mock
async def test_fetch_archive_daily_fails_fast_on_400() -> None:
    """Non-retryable 400 Bad Request raises HTTPStatusError without retrying."""
    route = respx.get(OPEN_METEO_ARCHIVE_URL).mock(
        return_value=httpx.Response(400, json={"error": True, "reason": "bad date"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_archive_daily(
            lat=39.7392,
            lon=-104.9903,
            timezone="America/Denver",
            start_date="2025-01-01",
            end_date="2025-01-02",
        )

    assert route.call_count == 1


async def test_upsert_weather_daily_idempotent(db_session: AsyncSession) -> None:
    """upsert_weather_daily inserts new rows and updates existing (hub_id, date) rows."""
    await _init_schema(db_session)
    await seed_hubs(db_session)

    hub = (await db_session.execute(select(Hub).where(Hub.code == "DEN"))).scalar_one()

    initial_rows = [
        {
            "date": date(2025, 1, 1),
            "temp_max_c": Decimal("4.5"),
            "temp_min_c": Decimal("-5.0"),
            "precip_mm": Decimal("0.00"),
            "snowfall_cm": Decimal("0.00"),
            "gust_max_kmh": Decimal("25.0"),
            "weather_code": 0,
        },
        {
            "date": date(2025, 1, 2),
            "temp_max_c": Decimal("-1.0"),
            "temp_min_c": Decimal("-9.5"),
            "precip_mm": Decimal("8.00"),
            "snowfall_cm": Decimal("6.00"),
            "gust_max_kmh": Decimal("42.0"),
            "weather_code": 73,
        },
    ]

    assert await upsert_weather_daily(db_session, hub.id, []) == 0
    count1 = await upsert_weather_daily(db_session, hub.id, initial_rows)
    assert count1 == 2

    updated_rows = [
        {
            "date": date(2025, 1, 2),
            "temp_max_c": Decimal("-0.5"),
            "temp_min_c": Decimal("-8.0"),
            "precip_mm": Decimal("10.50"),
            "snowfall_cm": Decimal("7.25"),
            "gust_max_kmh": Decimal("48.0"),
            "weather_code": 75,
        }
    ]
    count2 = await upsert_weather_daily(db_session, hub.id, updated_rows)
    assert count2 == 1

    stored = (
        await db_session.execute(
            select(WeatherDaily)
            .where(WeatherDaily.hub_id == hub.id)
            .order_by(WeatherDaily.date)
        )
    ).scalars().all()

    assert len(stored) == 2
    assert stored[1].temp_max_c == Decimal("-0.5")
    assert stored[1].snowfall_cm == Decimal("7.25")
    assert stored[1].weather_code == 75


async def test_fema_nri_load_and_apply_to_all_20_hubs(db_session: AsyncSession) -> None:
    """nri_hubs.json covers all 20 hubs in hubs.json and populates hubs.nri."""
    data_dir = Path(__file__).resolve().parent.parent / "app" / "data"
    with (data_dir / "hubs.json").open("r", encoding="utf-8") as f:
        hubs_json = json.load(f)

    nri_data = load_nri_data()
    assert len(nri_data) == 20

    expected_score_keys = {
        "composite",
        "hurricane",
        "riverine_flood",
        "coastal_flood",
        "winter_weather",
        "ice_storm",
        "cold_wave",
        "tornado",
        "hail",
        "strong_wind",
        "heat_wave",
    }
    for hub_entry in hubs_json:
        code = hub_entry["code"]
        assert code in nri_data
        entry = nri_data[code]
        assert entry.get("version")
        assert entry.get("county")
        assert set(entry["scores"].keys()) == expected_score_keys

    await _init_schema(db_session)
    await seed_hubs(db_session)

    # Clear nri on one hub, then verify apply_nri_to_hubs restores it.
    dfw = (await db_session.execute(select(Hub).where(Hub.code == "DFW"))).scalar_one()
    dfw.nri = None
    await db_session.commit()

    outcome = await apply_nri_to_hubs(db_session)
    assert outcome == {"updated": 20, "missing": 0}

    await db_session.refresh(dfw)
    assert dfw.nri is not None
    assert dfw.nri["county"] == "Dallas County"
    assert dfw.nri["scores"]["composite"] is not None


@respx.mock
async def test_nws_fetch_active_alerts_happy_path_and_user_agent() -> None:
    """NWS client sends User-Agent header and parses GeoJSON features."""
    nws_payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "properties": {
                    "event": "Hurricane Warning",
                    "severity": "Extreme",
                    "headline": "Hurricane Warning issued for Miami-Dade",
                    "expires": "2026-11-16T06:00:00-05:00",
                }
            },
            {
                "properties": {
                    "event": "Flood Watch",
                    "severity": "Moderate",
                    "headline": "Flood Watch in effect",
                    "expires": None,
                }
            },
        ],
    }
    route = respx.get(NWS_ALERTS_URL).mock(return_value=httpx.Response(200, json=nws_payload))

    alerts = await fetch_active_alerts(
        25.7617,
        -80.1918,
        user_agent="TestWeatherAgent (test@example.com)",
    )

    assert route.called
    sent_headers = route.calls[0].request.headers
    assert sent_headers["user-agent"] == "TestWeatherAgent (test@example.com)"
    assert "point=25.7617%2C-80.1918" in str(route.calls[0].request.url)

    assert len(alerts) == 2
    assert alerts[0] == {
        "event": "Hurricane Warning",
        "severity": "Extreme",
        "headline": "Hurricane Warning issued for Miami-Dade",
        "expires": "2026-11-16T06:00:00-05:00",
    }
    assert alerts[1]["event"] == "Flood Watch"


@respx.mock
async def test_nws_fetch_active_alerts_retries_and_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NWS client retries on 503 and returns [] if all attempts fail."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    route = respx.get(NWS_ALERTS_URL).mock(
        return_value=httpx.Response(503, text="Upstream error")
    )

    alerts = await fetch_active_alerts(25.7617, -80.1918)

    assert route.call_count == 3
    assert alerts == []


@respx.mock
async def test_pipeline_refresh_weather_and_fetch_alerts(db_session: AsyncSession) -> None:
    """Pipeline orchestrates weather refresh and NWS alerts across all seeded hubs."""
    await _init_schema(db_session)
    await seed_hubs(db_session)

    respx.get(OPEN_METEO_ARCHIVE_URL).mock(
        return_value=httpx.Response(200, json=_sample_open_meteo_payload())
    )
    respx.get(NWS_ALERTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "event": "Wind Advisory",
                            "severity": "Moderate",
                            "headline": "Gusty winds",
                            "expires": "2026-11-16T00:00:00Z",
                        }
                    }
                ]
            },
        )
    )

    weather_summary = await refresh_weather_all_hubs(
        db_session,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2),
    )
    assert weather_summary["hub_count"] == 20
    assert weather_summary["rows_upserted"] == 40
    assert weather_summary["errors"] == []
    assert all(h["coverage_pct"] == 100.0 for h in weather_summary["hubs"])

    alerts_summary = await fetch_alerts_all_hubs(db_session)
    assert alerts_summary["hub_count"] == 20
    assert alerts_summary["alerts_found"] == 20
    assert len(alerts_summary["alerts_by_hub"]["ORD"]) == 1
    assert alerts_summary["errors"] == []


def test_fetch_nri_transform_and_mapping() -> None:
    """transform_nri_feature supports both IFLD_RISKS and RFLD_RISKS and validates FIPS."""
    sample_ifld = {
        "STCOFIPS": "48113",
        "COUNTY": "Dallas",
        "COUNTYTYPE": "County",
        "NRI_VER": "December 2025",
        "RISK_SCORE": 99.841,
        "HRCN_RISKS": 48.123,
        "IFLD_RISKS": 98.765,
        "CFLD_RISKS": None,
        "WNTW_RISKS": 75.0,
        "ISTM_RISKS": 93.5,
        "CWAV_RISKS": 80.2,
        "TRND_RISKS": 99.1,
        "HAIL_RISKS": 98.7,
        "SWND_RISKS": 90.4,
        "HWAV_RISKS": 95.6,
    }
    transformed = transform_nri_feature(sample_ifld)
    assert transformed["version"] == "NRI December 2025"
    assert transformed["county"] == "Dallas County"
    assert transformed["scores"]["composite"] == 99.84
    assert transformed["scores"]["riverine_flood"] == 98.77
    assert transformed["scores"]["coastal_flood"] is None

    # Older schema using RFLD_RISKS
    sample_rfld = dict(sample_ifld)
    del sample_rfld["IFLD_RISKS"]
    sample_rfld["RFLD_RISKS"] = 88.0
    assert transform_nri_feature(sample_rfld)["scores"]["riverine_flood"] == 88.0

    mapped = build_nri_hubs_map(
        [{"code": "DFW", "county_fips": "48113"}],
        [{"attributes": sample_ifld}],
    )
    assert "DFW" in mapped

    with pytest.raises(RuntimeError, match="Missing FEMA NRI records"):
        build_nri_hubs_map(
            [{"code": "DFW", "county_fips": "48113"}, {"code": "MIA", "county_fips": "12086"}],
            [{"attributes": sample_ifld}],
        )


def test_compute_coverage_and_backfill_window() -> None:
    """Coverage calculation and backfill window handle edge cases cleanly."""
    rows = [
        {"temp_max_c": Decimal("10.0"), "temp_min_c": Decimal("2.0"), "precip_mm": Decimal("0.0")},
        {"temp_max_c": None, "temp_min_c": None, "precip_mm": None},
    ]
    assert compute_coverage_pct(rows, date(2025, 1, 1), date(2025, 1, 2)) == 50.0

    start, end = compute_backfill_window(5, today=date(2026, 11, 15))
    assert start == date(2021, 1, 1)
    assert end == date(2026, 11, 14)
