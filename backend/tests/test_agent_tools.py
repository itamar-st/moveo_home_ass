"""Integration tests for the 6 deterministic agent tools with M3 scoring outputs."""

from collections.abc import AsyncGenerator
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.tools import (
    build_artifacts,
    tool_compare_hubs,
    tool_explain_hub_risk,
    tool_get_active_alerts,
    tool_get_weather_stats,
    tool_list_hubs,
    tool_rank_hubs,
)
from app.db.models import Base, Hub, WeatherDaily
from app.schemas.tools import (
    CompareHubsArgs,
    ExplainHubRiskArgs,
    GetActiveAlertsArgs,
    GetWeatherStatsArgs,
    ListHubsArgs,
    RankHubsArgs,
)
from app.scoring.runner import rescore_all_hubs
from scripts.seed import seed_hubs


@pytest.fixture
async def scored_session() -> AsyncGenerator[AsyncSession, None]:
    """Seed all 20 hubs, insert weather rows, and run M3 `rescore_all_hubs`."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await seed_hubs(session)
        hubs = {h.code: h for h in (await session.execute(select(Hub))).scalars().all()}

        # Populate full 2025 calendar year (365 days) for key test hubs + 2021-2024 sample
        weather_rows: list[WeatherDaily] = []
        start_2025 = date(2025, 1, 1)
        for day_idx in range(365):
            cur_date = start_2025 + timedelta(days=day_idx)
            # MSP: heavy winter snow + hard freezes
            weather_rows.append(
                WeatherDaily(
                    hub_id=hubs["MSP"].id,
                    date=cur_date,
                    temp_max_c=Decimal("10.0"),
                    temp_min_c=Decimal("-15.0") if day_idx < 30 else Decimal("2.0"),
                    precip_mm=Decimal("2.0"),
                    snowfall_cm=Decimal("4.0") if day_idx < 20 else Decimal("0.0"),
                    gust_max_kmh=Decimal("35.0"),
                    weather_code=73 if day_idx < 20 else 3,
                )
            )
            # ORD: moderate winter snow (below MSP)
            weather_rows.append(
                WeatherDaily(
                    hub_id=hubs["ORD"].id,
                    date=cur_date,
                    temp_max_c=Decimal("12.0"),
                    temp_min_c=Decimal("-11.0") if day_idx < 10 else Decimal("4.0"),
                    precip_mm=Decimal("3.0"),
                    snowfall_cm=Decimal("3.0") if day_idx < 5 else Decimal("0.0"),
                    gust_max_kmh=Decimal("40.0"),
                    weather_code=3,
                )
            )
            # DEN: 73 days with >= 0.1 cm snow (exactly 20.0% of 365 days)
            weather_rows.append(
                WeatherDaily(
                    hub_id=hubs["DEN"].id,
                    date=cur_date,
                    temp_max_c=Decimal("18.0"),
                    temp_min_c=Decimal("-5.0"),
                    precip_mm=Decimal("1.0"),
                    snowfall_cm=Decimal("0.5") if day_idx < 73 else Decimal("0.0"),
                    gust_max_kmh=Decimal("30.0"),
                    weather_code=71 if day_idx < 73 else 1,
                )
            )
            # MIA: heavy precip + warm temps
            weather_rows.append(
                WeatherDaily(
                    hub_id=hubs["MIA"].id,
                    date=cur_date,
                    temp_max_c=Decimal("31.0"),
                    temp_min_c=Decimal("22.0"),
                    precip_mm=Decimal("30.0") if day_idx < 18 else Decimal("2.0"),
                    snowfall_cm=Decimal("0.0"),
                    gust_max_kmh=Decimal("85.0") if day_idx < 2 else Decimal("25.0"),
                    weather_code=63 if day_idx < 18 else 1,
                )
            )
            # IAH: heavy flood precip + heat
            weather_rows.append(
                WeatherDaily(
                    hub_id=hubs["IAH"].id,
                    date=cur_date,
                    temp_max_c=Decimal("38.5") if day_idx < 10 else Decimal("28.0"),
                    temp_min_c=Decimal("18.0"),
                    precip_mm=Decimal("28.0") if day_idx < 14 else Decimal("1.0"),
                    snowfall_cm=Decimal("0.0"),
                    gust_max_kmh=Decimal("35.0"),
                    weather_code=63 if day_idx < 14 else 1,
                )
            )
            # DFW: extreme heat + severe wind + occasional freeze
            weather_rows.append(
                WeatherDaily(
                    hub_id=hubs["DFW"].id,
                    date=cur_date,
                    temp_max_c=Decimal("39.0") if day_idx < 25 else Decimal("27.0"),
                    temp_min_c=Decimal("-2.0") if day_idx < 10 else Decimal("15.0"),
                    precip_mm=Decimal("26.0") if day_idx < 6 else Decimal("0.5"),
                    snowfall_cm=Decimal("0.0"),
                    gust_max_kmh=Decimal("82.0") if day_idx < 4 else Decimal("30.0"),
                    weather_code=66 if day_idx < 2 else 1,
                )
            )

        session.add_all(weather_rows)
        await session.commit()

        # Run M3's actual rescore_all_hubs with simulated NWS alert on MIA
        await rescore_all_hubs(
            session,
            fetch_nws=False,
            simulated_alerts_by_hub={
                "MIA": [
                    {
                        "event": "Hurricane Warning",
                        "severity": "Extreme",
                        "headline": "Hurricane Warning for Miami-Dade",
                        "expires": "2026-11-16T06:00:00Z",
                    }
                ]
            },
            computed_at=datetime(2026, 11, 15, 18, 0, 0),
            reference_date=date(2026, 11, 15),
        )
        yield session

    await engine.dispose()


async def test_tool_list_hubs_all_and_filtered(scored_session: AsyncSession) -> None:
    res_all = await tool_list_hubs(scored_session, ListHubsArgs())
    assert res_all.ok is True
    assert res_all.data["count"] == 20

    res_mw = await tool_list_hubs(scored_session, ListHubsArgs(region="Midwest"))
    assert res_mw.ok is True
    assert res_mw.data["count"] == 6
    assert {h["code"] for h in res_mw.data["hubs"]} == {"ORD", "MSP", "DTW", "IND", "MCI", "STL"}


async def test_tool_rank_hubs_midwest_winter_and_operational(scored_session: AsyncSession) -> None:
    res = await tool_rank_hubs(
        scored_session,
        RankHubsArgs(hazard="winter", region="Midwest", top_n=5),
    )
    assert res.ok is True
    items = res.data["items"]
    assert len(items) == 5
    # MSP has highest snow + freeze in fixture
    assert items[0]["code"] == "MSP"
    assert items[0]["rank"] == 1
    assert items[0]["score"] >= items[1]["score"]

    op_res = await tool_rank_hubs(
        scored_session,
        RankHubsArgs(hazard="overall", score_kind="operational", top_n=5),
    )
    assert op_res.ok is True
    mia_items = [i for i in op_res.data["items"] if i["code"] == "MIA"]
    assert len(mia_items) == 1
    assert mia_items[0]["operational_score"] > mia_items[0]["overall_score"]


async def test_tool_compare_hubs_miami_vs_houston_and_unknown(scored_session: AsyncSession) -> None:
    res = await tool_compare_hubs(
        scored_session,
        CompareHubsArgs(hubs=["Miami", "Houston"], hazards=["hurricane", "flood"]),
    )
    assert res.ok is True
    assert [h["code"] for h in res.data["hubs"]] == ["MIA", "IAH"]
    assert "hurricane" in res.data["winners"]
    assert "flood" in res.data["winners"]
    assert "overall" in res.data["winners"]

    bad = await tool_compare_hubs(
        scored_session,
        CompareHubsArgs(hubs=["Miami", "Boise"]),
    )
    assert bad.ok is False
    assert bad.data["unknown_hub"] == "Boise"
    assert len(bad.data["suggestions"]) >= 1


async def test_tool_explain_hub_risk_dallas_with_m3_weights(scored_session: AsyncSession) -> None:
    res = await tool_explain_hub_risk(scored_session, ExplainHubRiskArgs(hub="the Dallas hub"))
    assert res.ok is True
    assert res.data["code"] == "DFW"
    assert 1 <= res.data["rank"] <= 20
    assert res.data["total_hubs"] == 20
    assert "hazards" in res.data["details"]
    assert "drivers" in res.data["details"]
    assert res.data["scoring_weights"]["scoring_version"] == "wdri-v1"
    assert res.data["scoring_weights"]["caps"]["sig_snow_days"] == 12.0


async def test_tool_get_weather_stats_denver_q3(scored_session: AsyncSession) -> None:
    res = await tool_get_weather_stats(
        scored_session,
        GetWeatherStatsArgs(hub="Denver", metric="snowfall_days", period="last_year"),
        today=date(2026, 11, 15),
    )
    assert res.ok is True
    assert res.data["code"] == "DEN"
    assert res.data["days_matching"] == 73
    assert res.data["days_total"] == 365
    assert res.data["percentage"] == 20.0
    assert res.data["coverage_pct"] == 100.0
    assert res.data["period_resolved"] == ["2025-01-01", "2025-12-31"]


async def test_tool_get_active_alerts_and_artifacts(scored_session: AsyncSession) -> None:
    alerts_res = await tool_get_active_alerts(
        scored_session,
        GetActiveAlertsArgs(min_severity="Severe"),
    )
    assert alerts_res.ok is True
    assert alerts_res.data["total_alerts"] == 1
    assert alerts_res.data["alerts"][0]["hub"] == "MIA"
    assert alerts_res.data["alerts"][0]["event"] == "Hurricane Warning"

    rank_res = await tool_rank_hubs(scored_session, RankHubsArgs(hazard="winter", region="Midwest"))
    cmp_res = await tool_compare_hubs(scored_session, CompareHubsArgs(hubs=["MIA", "IAH"]))
    exp_res = await tool_explain_hub_risk(scored_session, ExplainHubRiskArgs(hub="DFW"))
    stat_res = await tool_get_weather_stats(
        scored_session,
        GetWeatherStatsArgs(hub="DEN", metric="snowfall_days", period="last_year"),
        today=date(2026, 11, 15),
    )

    artifacts = build_artifacts([rank_res, cmp_res, exp_res, stat_res, alerts_res])
    assert [a.type for a in artifacts] == [
        "ranking_table",
        "comparison",
        "score_breakdown",
        "stat",
        "alerts",
    ]
