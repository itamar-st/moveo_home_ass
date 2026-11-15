"""REST API integration tests for M3 hub, ranking, history, and scoring metadata routes."""

from datetime import date, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base, Hub, WeatherDaily
from app.scoring.config import SCORING_VERSION
from app.scoring.runner import rescore_all_hubs
from scripts.seed import seed_hubs


async def _init_and_seed(session: AsyncSession, *, with_weather: bool = True) -> None:
    conn = await session.connection()
    await conn.run_sync(Base.metadata.create_all)
    await seed_hubs(session)

    if not with_weather:
        return

    hubs = (await session.execute(select(Hub).order_by(Hub.code))).scalars().all()
    start = date(2021, 1, 1)
    end = date(2025, 12, 31)
    total_days = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(total_days)]

    profiles: dict[str, dict[str, Any]] = {
        "MSP": {"snow": 55, "freeze": 250, "precip": 30, "heat": 0, "wind": 2},
        "ORD": {"snow": 40, "freeze": 115, "precip": 30, "heat": 1, "wind": 7},
        "DTW": {"snow": 38, "freeze": 90, "precip": 25, "heat": 1, "wind": 8},
        "MCI": {"snow": 22, "freeze": 80, "precip": 55, "heat": 8, "wind": 7},
        "IND": {"snow": 20, "freeze": 70, "precip": 30, "heat": 0, "wind": 5},
        "STL": {"snow": 16, "freeze": 60, "precip": 45, "heat": 5, "wind": 4},
        "MIA": {"snow": 0, "freeze": 0, "precip": 46, "heat": 0, "wind": 3},
        "MSY": {"snow": 0, "freeze": 0, "precip": 82, "heat": 6, "wind": 11},
        "IAH": {"snow": 0, "freeze": 1, "precip": 115, "heat": 30, "wind": 4},
        "DFW": {"snow": 4, "freeze": 10, "precip": 81, "heat": 104, "wind": 4},
        "PHX": {"snow": 0, "freeze": 0, "precip": 15, "heat": 500, "wind": 1},
    }

    batch: list[WeatherDaily] = []
    for hub in hubs:
        p = profiles.get(
            hub.code,
            {"snow": 5, "freeze": 10, "precip": 20, "heat": 2, "wind": 2},
        )
        for idx, d in enumerate(dates):
            batch.append(
                WeatherDaily(
                    hub_id=hub.id,
                    date=d,
                    temp_max_c=40.0 if idx < p["heat"] else 20.0,
                    temp_min_c=-15.0 if idx < p["freeze"] else 8.0,
                    precip_mm=30.0 if idx < p["precip"] else 1.0,
                    snowfall_cm=4.0 if idx < p["snow"] else 0.0,
                    gust_max_kmh=85.0 if idx < p["wind"] else 25.0,
                    weather_code=73 if idx < p["snow"] else 0,
                )
            )
    session.add_all(batch)
    await session.commit()


async def test_list_hubs_empty_and_unscored(client: AsyncClient, db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    await conn.run_sync(Base.metadata.create_all)

    resp_empty = await client.get("/api/hubs")
    assert resp_empty.status_code == 200
    assert resp_empty.json() == []

    await seed_hubs(db_session)
    resp_unscored = await client.get("/api/hubs")
    assert resp_unscored.status_code == 200
    items = resp_unscored.json()
    assert len(items) == 20
    assert items[0]["overall_score"] is None
    assert items[0]["overall_tier"] is None


async def test_list_hubs_with_scores_and_region_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _init_and_seed(db_session)
    await rescore_all_hubs(
        db_session,
        fetch_nws=False,
        computed_at=datetime(2026, 11, 15, 12, 0, 0),
        reference_date=date(2026, 11, 15),
    )

    resp = await client.get("/api/hubs")
    assert resp.status_code == 200
    hubs = resp.json()
    assert len(hubs) == 20
    for h in hubs:
        assert h["name"] == f"{h['city']} Hub"
        assert 0.0 <= h["overall_score"] <= 100.0
        assert h["overall_tier"] in {"Low", "Moderate", "High", "Severe"}

    midwest_resp = await client.get("/api/hubs", params={"region": "Midwest"})
    assert midwest_resp.status_code == 200
    midwest = midwest_resp.json()
    assert len(midwest) == 6
    assert {h["code"] for h in midwest} == {"ORD", "MSP", "DTW", "IND", "MCI", "STL"}

    bad_region = await client.get("/api/hubs", params={"region": "Atlantis"})
    assert bad_region.status_code == 422


async def test_get_hub_detail_and_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _init_and_seed(db_session)
    await rescore_all_hubs(
        db_session,
        fetch_nws=False,
        computed_at=datetime(2026, 11, 15, 12, 0, 0),
        reference_date=date(2026, 11, 15),
    )

    resp = await client.get("/api/hubs/dfw")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "DFW"
    assert data["name"] == "Dallas Hub"
    assert data["nri"]["scores"]["hail"] == 100.0
    assert data["details"]["scoring_version"] == SCORING_VERSION
    assert set(data["details"]["hazards"].keys()) == {
        "winter",
        "flood",
        "hurricane",
        "severe_storm",
        "heat",
    }
    assert len(data["details"]["drivers"]) >= 1

    not_found = await client.get("/api/hubs/BOI")
    assert not_found.status_code == 404
    err = not_found.json()
    assert err["error"]["code"] == "hub_not_found"


async def test_get_hub_history_ordering_and_limit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _init_and_seed(db_session)
    await rescore_all_hubs(
        db_session,
        fetch_nws=False,
        computed_at=datetime(2026, 11, 15, 10, 0, 0),
        reference_date=date(2026, 11, 15),
    )
    await rescore_all_hubs(
        db_session,
        fetch_nws=False,
        simulated_alerts_by_hub={
            "MIA": [{"event": "Hurricane Warning", "severity": "Extreme", "simulated": True}]
        },
        computed_at=datetime(2026, 11, 15, 12, 0, 0),
        reference_date=date(2026, 11, 15),
    )
    await rescore_all_hubs(
        db_session,
        fetch_nws=False,
        computed_at=datetime(2026, 11, 15, 14, 0, 0),
        reference_date=date(2026, 11, 15),
    )

    resp = await client.get("/api/hubs/MIA/history", params={"limit": 2})
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 2
    assert history[0]["computed_at"] > history[1]["computed_at"]
    assert history[0]["live_bump"] == 0.0
    assert history[1]["live_bump"] == 30.0

    not_found = await client.get("/api/hubs/ZZZ/history")
    assert not_found.status_code == 404


async def test_rankings_all_hazards_regions_and_score_kinds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _init_and_seed(db_session)

    # Before scoring, rankings returns empty items gracefully
    empty_rank = await client.get("/api/rankings")
    assert empty_rank.status_code == 200
    assert empty_rank.json()["items"] == []
    assert empty_rank.json()["computed_at"] is None

    await rescore_all_hubs(
        db_session,
        fetch_nws=False,
        simulated_alerts_by_hub={
            "MIA": [{"event": "Hurricane Warning", "severity": "Extreme", "simulated": True}]
        },
        computed_at=datetime(2026, 11, 15, 15, 0, 0),
        reference_date=date(2026, 11, 15),
    )

    # Midwest winter rankings (DoD check)
    winter_mw = await client.get(
        "/api/rankings",
        params={"hazard": "winter", "region": "Midwest", "limit": 6},
    )
    assert winter_mw.status_code == 200
    w_data = winter_mw.json()
    assert w_data["hazard"] == "winter"
    assert w_data["scoring_version"] == SCORING_VERSION
    assert w_data["computed_at"] is not None
    assert [i["code"] for i in w_data["items"][:3]] == ["MSP", "ORD", "DTW"]
    assert w_data["items"][0]["rank"] == 1
    assert "metrics" in w_data["items"][0]["breakdown"]
    assert "nri" in w_data["items"][0]["breakdown"]
    assert w_data["items"][0]["top_driver"] is not None

    # Hurricane top-3
    hurr = await client.get("/api/rankings", params={"hazard": "hurricane", "limit": 3})
    assert hurr.status_code == 200
    assert {i["code"] for i in hurr.json()["items"]} == {"MSY", "IAH", "MIA"}

    # Heat top-2
    heat = await client.get("/api/rankings", params={"hazard": "heat", "limit": 2})
    assert heat.status_code == 200
    assert heat.json()["items"][0]["code"] == "PHX"
    assert heat.json()["items"][1]["code"] == "DFW"

    # Flood and severe_storm
    for hz in ("flood", "severe_storm"):
        r = await client.get("/api/rankings", params={"hazard": hz, "limit": 5})
        assert r.status_code == 200
        scores = [item["score"] for item in r.json()["items"]]
        assert scores == sorted(scores, reverse=True)

    # Operational vs structural overall ranking (MIA boosted by +30)
    struct = await client.get(
        "/api/rankings", params={"hazard": "overall", "score_kind": "structural"}
    )
    oper = await client.get(
        "/api/rankings", params={"hazard": "overall", "score_kind": "operational"}
    )
    assert struct.status_code == 200 and oper.status_code == 200
    mia_struct = next(i for i in struct.json()["items"] if i["code"] == "MIA")
    mia_oper = next(i for i in oper.json()["items"] if i["code"] == "MIA")
    assert mia_oper["score"] == round(min(100.0, mia_struct["score"] + 30.0), 2)
    assert oper.json()["items"][0]["code"] == "MIA"

    # Invalid hazard -> 422
    bad = await client.get("/api/rankings", params={"hazard": "earthquake"})
    assert bad.status_code == 422


async def test_meta_scoring_and_health_freshness(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _init_and_seed(db_session)
    await rescore_all_hubs(
        db_session,
        fetch_nws=False,
        computed_at=datetime(2026, 11, 15, 16, 0, 0),
        reference_date=date(2026, 11, 15),
    )

    meta_resp = await client.get("/api/meta/scoring")
    assert meta_resp.status_code == 200
    meta = meta_resp.json()
    assert meta["scoring_version"] == SCORING_VERSION
    assert meta["caps"]["sig_snow_days"] == 12.0
    assert meta["caps"]["hard_freeze_days"] == 25.0
    assert meta["caps"]["heavy_precip_days"] == 16.0
    assert meta["caps"]["extreme_heat_days"] == 30.0

    health_resp = await client.get("/api/health")
    assert health_resp.status_code == 200
    health = health_resp.json()
    assert health["db"] == "ok"
    assert health["last_scored_at"] is not None
    assert health["weather_data_until"] == "2025-12-31"
