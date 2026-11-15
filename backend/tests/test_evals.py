"""Unit tests for the M8 evaluation suite (cases, oracles, graders, report)."""

from collections.abc import AsyncGenerator
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Hub, WeatherDaily
from app.schemas.agent import FinalAnswer
from app.scoring.runner import rescore_all_hubs
from evals.graders import grade_case
from evals.oracles import oracle_hub_score, oracle_rank, oracle_weather_stat
from evals.run import build_summary, load_cases, render_markdown_report
from scripts.seed import seed_hubs


@pytest.fixture
async def eval_db() -> AsyncGenerator[AsyncSession, None]:
    """Seed 20 hubs, sample 2025 weather, and score all hubs."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await seed_hubs(session)
        hubs = {h.code: h for h in (await session.execute(select(Hub))).scalars().all()}

        rows: list[WeatherDaily] = []
        start_2025 = date(2025, 1, 1)
        for day_idx in range(365):
            cur = start_2025 + timedelta(days=day_idx)
            rows.append(
                WeatherDaily(
                    hub_id=hubs["MSP"].id,
                    date=cur,
                    temp_max_c=Decimal("8.0"),
                    temp_min_c=Decimal("-14.0") if day_idx < 30 else Decimal("3.0"),
                    precip_mm=Decimal("1.0"),
                    snowfall_cm=Decimal("4.0") if day_idx < 25 else Decimal("0.0"),
                    gust_max_kmh=Decimal("30.0"),
                    weather_code=73 if day_idx < 25 else 1,
                )
            )
            rows.append(
                WeatherDaily(
                    hub_id=hubs["DEN"].id,
                    date=cur,
                    temp_max_c=Decimal("15.0"),
                    temp_min_c=Decimal("-5.0") if day_idx < 20 else Decimal("5.0"),
                    precip_mm=Decimal("1.0"),
                    snowfall_cm=Decimal("1.0") if day_idx < 36 else Decimal("0.0"),
                    gust_max_kmh=Decimal("30.0"),
                    weather_code=71 if day_idx < 36 else 1,
                )
            )
        session.add_all(rows)
        await session.commit()
        await rescore_all_hubs(
            session,
            fetch_nws=False,
            computed_at=datetime(2026, 1, 1, 0, 0, 0),
            reference_date=date(2026, 3, 1),
        )
        yield session

    await engine.dispose()


def test_cases_yaml_loads_15_cases() -> None:
    """Verify cases.yaml loads and has unique IDs."""
    cases = load_cases()
    assert len(cases) >= 15
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    assert "q1_midwest_winter" in ids
    assert "q3_denver_snow_pct" in ids


async def test_oracles_and_graders_pass_on_valid_output(eval_db: AsyncSession) -> None:
    """Verify oracle_rank, oracle_weather_stat, oracle_hub_score, and grade_case."""
    today = date(2026, 3, 1)
    top3 = await oracle_rank(eval_db, hazard="winter", region="Midwest", k=3, today=today)
    assert len(top3) == 3
    assert top3[0] == "MSP"

    pct = await oracle_weather_stat(
        eval_db, hub="DEN", metric="snowfall_days", period="last_year", today=today
    )
    assert pct == pytest.approx(36 / 365 * 100, abs=0.2)

    dfw_score = await oracle_hub_score(eval_db, hub="DFW", today=today)
    assert dfw_score > 0

    case = next(c for c in load_cases() if c["id"] == "q3_denver_snow_pct")
    answer = FinalAnswer(
        answer_markdown=f"In calendar year 2025, Denver (DEN) had snowfall on {pct:.1f}% of days (36 of 365 days).",
        intent="statistic",
        hubs=["DEN"],
        key_points=[f"Denver snowfall frequency in 2025 was {pct:.1f}%."],
        reasoning=["Computed from daily Open-Meteo reanalysis rows for 2025."],
        assumptions=["Last year interpreted as calendar year 2025."],
        confidence="high",
        confidence_rationale="Full 365-day coverage.",
        follow_ups=["Compare Denver with Minneapolis"],
    )
    tool_trace = [
        {
            "name": "get_weather_stats",
            "ok": True,
            "arguments": {"hub": "DEN", "metric": "snowfall_days", "period": "last_year"},
            "data": {"percentage": round(pct, 2), "days_matching": 36},
        }
    ]
    res = await grade_case(
        eval_db,
        case,
        raw_answer=answer,
        tool_trace=tool_trace,
        artifacts=[],
        warnings=[],
        schema_repairs=0,
        used_fallback=False,
        model="test/model",
        latency_ms=120,
        today=today,
    )
    assert res.passed is True
    assert res.schema_valid is True

    summary = build_summary([res], requested_model="test/model")
    assert summary["pass_rate_pct"] == 100.0
    assert summary["schema_valid_pct"] == 100.0
    md = render_markdown_report(summary, [res])
    assert "q3_denver_snow_pct" in md
    assert "PASS" in md
