"""Deterministic DB-backed oracles for evaluation assertions (AGENTS.md §9.2)."""

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import (
    tool_explain_hub_risk,
    tool_get_weather_stats,
    tool_rank_hubs,
)
from app.schemas.tools import (
    ExplainHubRiskArgs,
    GetWeatherStatsArgs,
    RankHubsArgs,
)


async def oracle_rank(
    db: AsyncSession,
    *,
    hazard: str = "overall",
    region: str | None = None,
    score_kind: str = "structural",
    k: int = 3,
    today: date | None = None,
) -> list[str]:
    """Return the deterministic top-k hub codes for the given ranking parameters."""
    args = RankHubsArgs(
        hazard=hazard,  # type: ignore[arg-type]
        region=region,  # type: ignore[arg-type]
        score_kind=score_kind,  # type: ignore[arg-type]
        top_n=max(1, min(20, k)),
    )
    res = await tool_rank_hubs(db, args, today=today)
    if not res.ok:
        raise RuntimeError(f"Oracle rank failed: {res.summary}")
    items = res.data.get("items") or []
    return [str(item["code"]).upper() for item in items[:k]]


async def oracle_weather_stat(
    db: AsyncSession,
    *,
    hub: str,
    metric: str,
    period: str = "last_year",
    start_date: str | None = None,
    end_date: str | None = None,
    field: str = "percentage",
    today: date | None = None,
) -> float:
    """Return a deterministic weather statistic (`percentage`, `days_matching`, or `value`)."""
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    args = GetWeatherStatsArgs(
        hub=hub,
        metric=metric,  # type: ignore[arg-type]
        period=period,  # type: ignore[arg-type]
        start_date=parsed_start,
        end_date=parsed_end,
    )
    res = await tool_get_weather_stats(db, args, today=today)
    if not res.ok:
        raise RuntimeError(f"Oracle weather_stat failed: {res.summary}")
    val = res.data.get(field)
    if val is None:
        val = res.data.get("value")
    if val is None:
        raise RuntimeError(f"Oracle weather_stat missing field '{field}' in {res.data}")
    return float(val)


async def oracle_hub_score(
    db: AsyncSession,
    *,
    hub: str,
    hazard: str | None = None,
    today: date | None = None,
) -> float:
    """Return a hub's deterministic overall_score or specific hazard score."""
    res = await tool_explain_hub_risk(db, ExplainHubRiskArgs(hub=hub), today=today)
    if not res.ok:
        raise RuntimeError(f"Oracle hub_score failed for {hub}: {res.summary}")
    if hazard is None or hazard == "overall":
        return float(res.data["overall_score"])
    hazards = (res.data.get("details") or {}).get("hazards") or {}
    entry = hazards.get(hazard) or {}
    if "score" not in entry:
        raise RuntimeError(f"Oracle hub_score missing hazard '{hazard}' for {hub}")
    return float(entry["score"])


async def resolve_numeric_oracle(
    db: AsyncSession,
    spec: dict[str, Any],
    *,
    today: date | None = None,
) -> float:
    """Dispatch a numeric oracle specification from cases.yaml."""
    kind = spec["oracle"]
    if kind == "weather_pct":
        return await oracle_weather_stat(
            db,
            hub=spec["hub"],
            metric=spec["metric"],
            period=spec.get("period", "last_year"),
            start_date=spec.get("start_date"),
            end_date=spec.get("end_date"),
            field="percentage",
            today=today,
        )
    if kind == "weather_days":
        return await oracle_weather_stat(
            db,
            hub=spec["hub"],
            metric=spec["metric"],
            period=spec.get("period", "last_year"),
            start_date=spec.get("start_date"),
            end_date=spec.get("end_date"),
            field="days_matching",
            today=today,
        )
    if kind == "weather_value":
        return await oracle_weather_stat(
            db,
            hub=spec["hub"],
            metric=spec["metric"],
            period=spec.get("period", "last_year"),
            start_date=spec.get("start_date"),
            end_date=spec.get("end_date"),
            field="value",
            today=today,
        )
    if kind == "hub_overall_score":
        return await oracle_hub_score(db, hub=spec["hub"], hazard=None, today=today)
    if kind == "hub_hazard_score":
        return await oracle_hub_score(
            db,
            hub=spec["hub"],
            hazard=spec["hazard"],
            today=today,
        )
    raise ValueError(f"Unknown numeric oracle type: {kind!r}")
