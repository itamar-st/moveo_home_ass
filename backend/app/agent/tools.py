"""Deterministic tool registry, implementations, and artifact builders."""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.hub_resolver import (
    HubResolver,
    format_unknown_hub_message,
)
from app.agent.scoring_bridge import (
    get_scoring_version,
    get_scoring_weights_summary,
    load_latest_snapshots_by_hub,
    score_to_tier,
)
from app.db.models import Hub, RiskSnapshot
from app.db.repositories import get_weather_range
from app.schemas.tools import (
    Artifact,
    CompareHubsArgs,
    ExplainHubRiskArgs,
    GetActiveAlertsArgs,
    GetWeatherStatsArgs,
    ListHubsArgs,
    RankHubsArgs,
    ToolResult,
)
from app.scoring.config import DEFAULT_SCORING_CONFIG
from app.scoring.metrics import compute_period_stat, resolve_period

logger = logging.getLogger(__name__)

ALL_HAZARDS = ("winter", "hurricane", "flood", "severe_storm", "heat")
SEVERITY_RANK = {"Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}

DEFAULT_CAVEATS = [
    "Open-Meteo archive is ERA5/ERA5-Land reanalysis (~9-25 km grid), not station observations.",
    "FEMA NRI scores are county-level percentiles and average over large counties.",
]


def _extract_hazard_score(details: dict[str, Any], hazard: str) -> float:
    hazards = details.get("hazards") or {}
    entry = hazards.get(hazard) or {}
    return round(float(entry.get("score") or 0.0), 2)


def _extract_top_driver(details: dict[str, Any], hazard: str) -> str:
    drivers = details.get("drivers") or []
    if hazard == "overall":
        if drivers:
            return str(drivers[0])
        hazards = details.get("hazards") or {}
        if hazards:
            top_h = max(hazards.items(), key=lambda kv: float((kv[1] or {}).get("score") or 0.0))
            return f"{top_h[0]}: {float((top_h[1] or {}).get('score') or 0.0):.1f}/100"
        return "N/A"

    for drv in drivers:
        if str(drv).lower().startswith(hazard.lower()):
            return str(drv)

    hazard_entry = (details.get("hazards") or {}).get(hazard) or {}
    metrics = hazard_entry.get("metrics") or {}
    if metrics:
        top_m = max(metrics.items(), key=lambda kv: float(kv[1] or 0.0))
        return f"{top_m[0]}: {top_m[1]}"
    nri = hazard_entry.get("nri") or {}
    if nri:
        top_n = max(nri.items(), key=lambda kv: float(kv[1] or 0.0))
        return f"{top_n[0]} NRI: {top_n[1]}"
    return f"{hazard} score: {_extract_hazard_score(details, hazard):.1f}"


def _snapshot_caveats(snapshot: RiskSnapshot) -> list[str]:
    details = snapshot.details or {}
    caveats = [str(c) for c in (details.get("caveats") or [])]
    if not caveats:
        caveats = list(DEFAULT_CAVEATS)
    confidence = details.get("confidence")
    if confidence and confidence != "high":
        caveats.append(f"Snapshot confidence is {confidence} (partial coverage or missing NRI field).")
    return list(dict.fromkeys(caveats))


async def tool_list_hubs(
    db: AsyncSession,
    args: ListHubsArgs,
    *,
    today: date | None = None,
) -> ToolResult:
    """List portfolio hubs, optionally filtered by region, with current WDRI scores."""
    pairs = await load_latest_snapshots_by_hub(db, region=args.region)
    hub_items: list[dict[str, Any]] = []
    window: list[str] | None = None
    caveats: list[str] = []

    for hub, snap in pairs:
        overall = float(snap.overall_score) if snap is not None else None
        operational = float(snap.operational_score) if snap is not None else None
        if snap is not None and window is None:
            w = (snap.details or {}).get("window")
            if isinstance(w, list) and len(w) == 2:
                window = [str(w[0]), str(w[1])]
        hub_items.append(
            {
                "code": hub.code,
                "name": f"{hub.city} Hub",
                "city": hub.city,
                "state": hub.state,
                "region": hub.region,
                "overall_score": round(overall, 2) if overall is not None else None,
                "overall_tier": score_to_tier(overall) if overall is not None else None,
                "operational_score": round(operational, 2) if operational is not None else None,
                "operational_tier": score_to_tier(operational) if operational is not None else None,
            }
        )

    if any(item["overall_score"] is None for item in hub_items):
        caveats.append("One or more hubs do not have a computed risk snapshot yet.")

    region_label = args.region or "all regions"
    return ToolResult(
        ok=True,
        tool_name="list_hubs",
        summary=f"Found {len(hub_items)} hubs in {region_label}.",
        data={"region": args.region, "count": len(hub_items), "hubs": hub_items},
        caveats=caveats or ["Portfolio contains 20 fixed US logistics hubs."],
        sources=["Hubs roster", f"RiskSnapshot ({get_scoring_version()})"],
        data_window=window,
    )


async def tool_rank_hubs(
    db: AsyncSession,
    args: RankHubsArgs,
    *,
    today: date | None = None,
) -> ToolResult:
    """Rank hubs by overall WDRI or a specific hazard score."""
    pairs = await load_latest_snapshots_by_hub(db, region=args.region)
    scored_rows: list[tuple[Hub, RiskSnapshot, float]] = []

    for hub, snap in pairs:
        if snap is None:
            continue
        details = snap.details or {}
        if args.hazard == "overall":
            val = (
                float(snap.operational_score)
                if args.score_kind == "operational"
                else float(snap.overall_score)
            )
        else:
            val = _extract_hazard_score(details, args.hazard)
        scored_rows.append((hub, snap, val))

    if not scored_rows:
        return ToolResult(
            ok=False,
            tool_name="rank_hubs",
            summary="No risk snapshots available yet to rank hubs. Run scoring first.",
            data={"hazard": args.hazard, "region": args.region, "items": []},
            caveats=["No computed risk_snapshots rows found in database."],
            sources=["RiskSnapshot"],
        )

    scored_rows.sort(
        key=lambda item: (-item[2], -float(item[1].overall_score), item[0].code),
    )

    top_rows = scored_rows[: args.top_n]
    items: list[dict[str, Any]] = []
    caveats: list[str] = []
    window: list[str] | None = None
    computed_at: str | None = None
    scoring_version = get_scoring_version()

    for idx, (hub, snap, score_val) in enumerate(top_rows, start=1):
        details = snap.details or {}
        if computed_at is None:
            computed_at = snap.computed_at.isoformat()
        if details.get("scoring_version"):
            scoring_version = str(details["scoring_version"])
        if window is None and isinstance(details.get("window"), list):
            window = [str(x) for x in details["window"]]
        for c in _snapshot_caveats(snap):
            if c not in caveats:
                caveats.append(c)

        items.append(
            {
                "rank": idx,
                "code": hub.code,
                "city": hub.city,
                "state": hub.state,
                "region": hub.region,
                "score": round(score_val, 2),
                "tier": score_to_tier(score_val),
                "overall_score": round(float(snap.overall_score), 2),
                "operational_score": round(float(snap.operational_score), 2),
                "top_driver": _extract_top_driver(details, args.hazard),
            }
        )

    if args.hazard != "overall" and args.score_kind == "operational":
        caveats.append(
            "Live NWS operational bump applies to overall operational_score; "
            f"'{args.hazard}' sub-score reflects structural exposure."
        )

    region_label = args.region or "all regions"
    top_preview = ", ".join(f"{i['code']} ({i['score']:.1f})" for i in items[:3])
    return ToolResult(
        ok=True,
        tool_name="rank_hubs",
        summary=(
            f"Ranked top {len(items)} of {len(scored_rows)} hubs in {region_label} "
            f"by {args.hazard} ({args.score_kind}): {top_preview}."
        ),
        data={
            "hazard": args.hazard,
            "region": args.region,
            "score_kind": args.score_kind,
            "scoring_version": scoring_version,
            "computed_at": computed_at,
            "total_matching_hubs": len(scored_rows),
            "items": items,
        },
        caveats=caveats,
        sources=[f"RiskSnapshot ({scoring_version})", "FEMA NRI", "Open-Meteo Archive"],
        data_window=window,
    )


async def tool_compare_hubs(
    db: AsyncSession,
    args: CompareHubsArgs,
    *,
    today: date | None = None,
) -> ToolResult:
    """Compare 2-5 hubs side-by-side across overall and specific hazard scores."""
    pairs = await load_latest_snapshots_by_hub(db)
    hub_by_code = {h.code: (h, s) for h, s in pairs}
    resolver = HubResolver([h for h, _ in pairs])

    resolved_hubs: list[tuple[Hub, RiskSnapshot | None]] = []
    seen_codes: set[str] = set()
    for raw_name in args.hubs:
        res = resolver.resolve(raw_name)
        if not res.matched or res.hub is None:
            err = format_unknown_hub_message(raw_name, res.suggestions, len(pairs))
            return ToolResult(
                ok=False,
                tool_name="compare_hubs",
                summary=err["summary"],
                data=err["data"],
                caveats=["Only the 20 portfolio hubs can be compared."],
                sources=["Hubs roster"],
            )
        if res.hub.code not in seen_codes:
            seen_codes.add(res.hub.code)
            resolved_hubs.append(hub_by_code[res.hub.code])

    if len(resolved_hubs) < 2:
        return ToolResult(
            ok=False,
            tool_name="compare_hubs",
            summary="Need at least 2 distinct portfolio hubs to compare.",
            data={"hubs": list(seen_codes)},
            sources=["Hubs roster"],
        )

    missing_snaps = [h.code for h, s in resolved_hubs if s is None]
    if missing_snaps:
        return ToolResult(
            ok=False,
            tool_name="compare_hubs",
            summary=f"Missing risk snapshots for hubs: {', '.join(missing_snaps)}. Run scoring first.",
            data={"missing_snapshots": missing_snaps},
            sources=["RiskSnapshot"],
        )

    requested_hazards = list(args.hazards) if args.hazards else ["overall", *ALL_HAZARDS]
    if "overall" not in requested_hazards:
        comparison_keys = ["overall", *requested_hazards]
    else:
        comparison_keys = requested_hazards

    hub_entries: list[dict[str, Any]] = []
    caveats: list[str] = []
    window: list[str] | None = None

    for hub, snap in resolved_hubs:
        assert snap is not None
        details = snap.details or {}
        if window is None and isinstance(details.get("window"), list):
            window = [str(x) for x in details["window"]]
        for c in _snapshot_caveats(snap):
            if c not in caveats:
                caveats.append(c)

        hazard_scores: dict[str, float] = {"overall": round(float(snap.overall_score), 2)}
        hazard_details: dict[str, Any] = {}
        for h_name in ALL_HAZARDS:
            h_entry = (details.get("hazards") or {}).get(h_name) or {}
            h_score = round(float(h_entry.get("score") or 0.0), 2)
            hazard_scores[h_name] = h_score
            if h_name in comparison_keys:
                hazard_details[h_name] = {
                    "score": h_score,
                    "tier": score_to_tier(h_score),
                    "contribution": round(float(h_entry.get("contribution") or 0.0), 2),
                    "metrics": h_entry.get("metrics") or {},
                    "nri": h_entry.get("nri") or {},
                }

        hub_entries.append(
            {
                "code": hub.code,
                "city": hub.city,
                "state": hub.state,
                "region": hub.region,
                "overall_score": round(float(snap.overall_score), 2),
                "overall_tier": score_to_tier(float(snap.overall_score)),
                "operational_score": round(float(snap.operational_score), 2),
                "operational_tier": score_to_tier(float(snap.operational_score)),
                "confidence": details.get("confidence", "unknown"),
                "hazard_scores": {k: hazard_scores[k] for k in comparison_keys if k in hazard_scores},
                "hazard_details": hazard_details,
                "drivers": details.get("drivers") or [],
                "active_alerts": details.get("active_alerts") or [],
            }
        )

    winners: dict[str, dict[str, Any]] = {}
    for key in comparison_keys:
        best_val = max(entry["hazard_scores"][key] for entry in hub_entries)
        tied = [entry["code"] for entry in hub_entries if entry["hazard_scores"][key] == best_val]
        winners[key] = {
            "highest_exposure_hub": tied[0],
            "score": best_val,
            "tied_hubs": tied,
        }

    codes_str = " vs ".join(e["code"] for e in hub_entries)
    return ToolResult(
        ok=True,
        tool_name="compare_hubs",
        summary=(
            f"Compared {codes_str} across {', '.join(comparison_keys)}. "
            + "; ".join(
                f"{k}: highest {w['highest_exposure_hub']} ({w['score']:.1f})"
                for k, w in winners.items()
            )
            + "."
        ),
        data={
            "hazards": comparison_keys,
            "hubs": hub_entries,
            "winners": winners,
        },
        caveats=caveats,
        sources=[f"RiskSnapshot ({get_scoring_version()})", "FEMA NRI", "Open-Meteo Archive"],
        data_window=window,
    )


async def tool_explain_hub_risk(
    db: AsyncSession,
    args: ExplainHubRiskArgs,
    *,
    today: date | None = None,
) -> ToolResult:
    """Return full WDRI score breakdown, contributions, drivers, and weights for one hub."""
    pairs = await load_latest_snapshots_by_hub(db)
    resolver = HubResolver([h for h, _ in pairs])
    res = resolver.resolve(args.hub)
    if not res.matched or res.hub is None:
        err = format_unknown_hub_message(args.hub, res.suggestions, len(pairs))
        return ToolResult(
            ok=False,
            tool_name="explain_hub_risk",
            summary=err["summary"],
            data=err["data"],
            caveats=["Only the 20 portfolio hubs are scored."],
            sources=["Hubs roster"],
        )

    target_hub = res.hub
    scored_pairs = [(h, s) for h, s in pairs if s is not None]
    scored_pairs.sort(key=lambda item: (-float(item[1].overall_score), item[0].code))

    target_snap: RiskSnapshot | None = None
    rank: int | None = None
    for idx, (h, s) in enumerate(scored_pairs, start=1):
        if h.code == target_hub.code:
            target_snap = s
            rank = idx
            break

    if target_snap is None:
        return ToolResult(
            ok=False,
            tool_name="explain_hub_risk",
            summary=f"No risk snapshot found for {target_hub.city} ({target_hub.code}). Run scoring first.",
            data={"code": target_hub.code},
            sources=["RiskSnapshot"],
        )

    details = target_snap.details or {}
    overall = round(float(target_snap.overall_score), 2)
    operational = round(float(target_snap.operational_score), 2)
    window = details.get("window")
    data_window = [str(x) for x in window] if isinstance(window, list) else None
    drivers = details.get("drivers") or []
    top_drivers_str = "; ".join(str(d) for d in drivers[:3]) if drivers else "see hazard breakdown"

    return ToolResult(
        ok=True,
        tool_name="explain_hub_risk",
        summary=(
            f"{target_hub.city} ({target_hub.code}) WDRI is {overall:.2f} ({score_to_tier(overall)}, "
            f"rank {rank}/{len(scored_pairs)}), operational {operational:.2f}. "
            f"Top drivers: {top_drivers_str}."
        ),
        data={
            "code": target_hub.code,
            "name": f"{target_hub.city} Hub",
            "city": target_hub.city,
            "state": target_hub.state,
            "region": target_hub.region,
            "rank": rank,
            "total_hubs": len(scored_pairs),
            "computed_at": target_snap.computed_at.isoformat(),
            "overall_score": overall,
            "overall_tier": score_to_tier(overall),
            "operational_score": operational,
            "operational_tier": score_to_tier(operational),
            "details": details,
            "nri": target_hub.nri,
            "scoring_weights": get_scoring_weights_summary(),
        },
        caveats=_snapshot_caveats(target_snap),
        sources=[
            f"RiskSnapshot ({details.get('scoring_version', get_scoring_version())})",
            "FEMA NRI",
            "Open-Meteo Archive",
        ],
        data_window=data_window,
    )


async def tool_get_weather_stats(
    db: AsyncSession,
    args: GetWeatherStatsArgs,
    *,
    today: date | None = None,
) -> ToolResult:
    """Compute deterministic daily weather statistics via M3 scoring.metrics."""
    hubs = (await db.execute(select(Hub).order_by(Hub.code))).scalars().all()
    resolver = HubResolver(hubs)
    res = resolver.resolve(args.hub)
    if not res.matched or res.hub is None:
        err = format_unknown_hub_message(args.hub, res.suggestions, len(hubs))
        return ToolResult(
            ok=False,
            tool_name="get_weather_stats",
            summary=err["summary"],
            data=err["data"],
            caveats=["Weather history is stored only for the 20 portfolio hubs."],
            sources=["Hubs roster"],
        )

    hub = res.hub
    ref_today = today or date.today()
    start_d, end_d = resolve_period(
        args.period,
        reference_date=ref_today,
        start_date=args.start_date,
        end_date=args.end_date,
        years=DEFAULT_SCORING_CONFIG.climatology_years,
    )

    rows = await get_weather_range(db, hub.id, start_d, end_d)
    expected_days = max((end_d - start_d).days + 1, 1)
    if not rows:
        return ToolResult(
            ok=False,
            tool_name="get_weather_stats",
            summary=(
                f"No weather_daily observations found for {hub.city} ({hub.code}) "
                f"in {start_d.isoformat()} to {end_d.isoformat()}."
            ),
            data={
                "code": hub.code,
                "city": hub.city,
                "metric": args.metric,
                "period": args.period,
                "period_resolved": [start_d.isoformat(), end_d.isoformat()],
                "expected_days": expected_days,
                "days_total": 0,
                "coverage_pct": 0.0,
            },
            caveats=["Backfill weather data may be missing for this period."],
            sources=["Open-Meteo Archive (weather_daily)"],
            data_window=[start_d.isoformat(), end_d.isoformat()],
        )

    stat = compute_period_stat(
        rows,
        args.metric,
        start_date=start_d,
        end_date=end_d,
        config=DEFAULT_SCORING_CONFIG,
    )
    is_total_metric = args.metric in {"total_snowfall_cm", "total_precip_mm"}
    unit = "cm" if args.metric == "total_snowfall_cm" else "mm" if args.metric == "total_precip_mm" else "days"
    value: float | int = stat.value if is_total_metric else stat.days_matching

    caveats = [
        "Open-Meteo archive uses ERA5/ERA5-Land reanalysis (~9-25 km grid), not station observations.",
    ]
    if stat.coverage_pct < 95.0:
        caveats.append(
            f"Data coverage for this period is {stat.coverage_pct:.1f}% ({stat.days_total}/{expected_days} days)."
        )

    if is_total_metric:
        summary = (
            f"{hub.city} ({hub.code}) {args.metric} over {start_d.isoformat()}..{end_d.isoformat()}: "
            f"{value:.2f} {unit} across {stat.days_matching}/{stat.days_total} nonzero days."
        )
    else:
        summary = (
            f"{hub.city} ({hub.code}) had {stat.days_matching} {args.metric} out of {stat.days_total} observed days "
            f"({stat.percentage:.2f}%) in {start_d.isoformat()}..{end_d.isoformat()} "
            f"(threshold: {stat.threshold})."
        )

    return ToolResult(
        ok=True,
        tool_name="get_weather_stats",
        summary=summary,
        data={
            "code": hub.code,
            "city": hub.city,
            "state": hub.state,
            "metric": args.metric,
            "period": args.period,
            "value": value,
            "unit": unit,
            "days_matching": stat.days_matching,
            "days_total": stat.days_total,
            "expected_days": expected_days,
            "percentage": stat.percentage,
            "threshold": stat.threshold,
            "period_resolved": [start_d.isoformat(), end_d.isoformat()],
            "coverage_pct": stat.coverage_pct,
        },
        caveats=caveats,
        sources=["Open-Meteo Archive (weather_daily)"],
        data_window=[start_d.isoformat(), end_d.isoformat()],
    )


def _get_simulated_alerts_for_hub(hub_code: str) -> list[dict[str, Any]]:
    """Optionally read unexpired M7 simulated alerts if app.alerts.simulated exists."""
    try:
        sim_mod = import_module("app.alerts.simulated")
        getter = getattr(sim_mod, "get_active_simulated_alerts", None)
        if callable(getter):
            return list(getter(hub_code))
    except ModuleNotFoundError:
        return []
    return []


async def tool_get_active_alerts(
    db: AsyncSession,
    args: GetActiveAlertsArgs,
    *,
    today: date | None = None,
) -> ToolResult:
    """Return active NWS (and simulated demo) alerts and current operational scores."""
    pairs = await load_latest_snapshots_by_hub(db)
    target_code: str | None = None
    if args.hub is not None:
        resolver = HubResolver([h for h, _ in pairs])
        res = resolver.resolve(args.hub)
        if not res.matched or res.hub is None:
            err = format_unknown_hub_message(args.hub, res.suggestions, len(pairs))
            return ToolResult(
                ok=False,
                tool_name="get_active_alerts",
                summary=err["summary"],
                data=err["data"],
                sources=["Hubs roster"],
            )
        target_code = res.hub.code

    min_rank = SEVERITY_RANK.get(args.min_severity, 0) if args.min_severity else 0
    alert_items: list[dict[str, Any]] = []
    hubs_with_alerts: list[str] = []
    latest_computed_at: str | None = None

    for hub, snap in pairs:
        if target_code is not None and hub.code != target_code:
            continue
        if snap is None:
            continue
        if latest_computed_at is None:
            latest_computed_at = snap.computed_at.isoformat()

        details = snap.details or {}
        raw_alerts = list(details.get("active_alerts") or [])
        for sim in _get_simulated_alerts_for_hub(hub.code):
            if sim not in raw_alerts:
                raw_alerts.append(sim)

        matched_for_hub = False
        for alert in raw_alerts:
            sev = str(alert.get("severity") or "Unknown")
            if SEVERITY_RANK.get(sev, 0) < min_rank:
                continue
            matched_for_hub = True
            alert_items.append(
                {
                    "hub": hub.code,
                    "city": hub.city,
                    "overall_score": round(float(snap.overall_score), 2),
                    "operational_score": round(float(snap.operational_score), 2),
                    "operational_tier": score_to_tier(float(snap.operational_score)),
                    "live_bump": details.get("live_bump", 0),
                    "event": alert.get("event"),
                    "severity": sev,
                    "headline": alert.get("headline"),
                    "expires": alert.get("expires"),
                }
            )
        if matched_for_hub:
            hubs_with_alerts.append(hub.code)

    alert_items.sort(
        key=lambda a: (-SEVERITY_RANK.get(str(a.get("severity")), 0), str(a.get("hub"))),
    )

    scope_str = target_code or "all 20 hubs"
    return ToolResult(
        ok=True,
        tool_name="get_active_alerts",
        summary=f"Found {len(alert_items)} active alert(s) across {scope_str}.",
        data={
            "hub_filter": target_code,
            "min_severity": args.min_severity,
            "computed_at": latest_computed_at,
            "total_alerts": len(alert_items),
            "hubs_with_alerts": hubs_with_alerts,
            "alerts": alert_items,
        },
        caveats=[
            "NWS alerts are point-in-time signals embedded at the latest scoring run.",
        ],
        sources=["NWS Alerts API", "RiskSnapshot"],
    )


ToolHandler = Callable[..., Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    """Metadata and callable for a registered agent tool."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "list_hubs": ToolSpec(
        name="list_hubs",
        description="List portfolio hubs with codes, cities, regions, and current WDRI scores.",
        args_model=ListHubsArgs,
        handler=tool_list_hubs,
    ),
    "rank_hubs": ToolSpec(
        name="rank_hubs",
        description="Rank hubs by overall WDRI or a specific hazard (winter, hurricane, flood, severe_storm, heat).",
        args_model=RankHubsArgs,
        handler=tool_rank_hubs,
    ),
    "compare_hubs": ToolSpec(
        name="compare_hubs",
        description="Compare 2 to 5 hubs across overall and hazard-specific risk scores, metrics, and NRI inputs.",
        args_model=CompareHubsArgs,
        handler=tool_compare_hubs,
    ),
    "explain_hub_risk": ToolSpec(
        name="explain_hub_risk",
        description="Explain a single hub's WDRI score with hazard contributions, raw metrics, NRI inputs, and top drivers.",
        args_model=ExplainHubRiskArgs,
        handler=tool_explain_hub_risk,
    ),
    "get_weather_stats": ToolSpec(
        name="get_weather_stats",
        description="Compute deterministic daily weather statistics (counts, percentages, totals) over a date range.",
        args_model=GetWeatherStatsArgs,
        handler=tool_get_weather_stats,
    ),
    "get_active_alerts": ToolSpec(
        name="get_active_alerts",
        description="List active NWS weather alerts and live operational risk bumps for one or all hubs.",
        args_model=GetActiveAlertsArgs,
        handler=tool_get_active_alerts,
    ),
}


def build_artifacts(tool_results: Sequence[ToolResult]) -> list[Artifact]:
    """Build deterministic UI artifacts exclusively from successful tool outputs."""
    artifacts: list[Artifact] = []
    for res in tool_results:
        if not res.ok:
            continue
        data = res.data
        if res.tool_name == "rank_hubs":
            hazard = str(data.get("hazard") or "overall").replace("_", " ").title()
            region = data.get("region") or "All Regions"
            artifacts.append(
                Artifact(
                    type="ranking_table",
                    title=f"{hazard} Risk Ranking — {region}",
                    data=data,
                )
            )
        elif res.tool_name == "compare_hubs":
            codes = [str(h.get("code")) for h in (data.get("hubs") or [])]
            artifacts.append(
                Artifact(
                    type="comparison",
                    title=f"Hub Comparison: {' vs '.join(codes)}",
                    data=data,
                )
            )
        elif res.tool_name == "explain_hub_risk":
            city = data.get("city") or data.get("code")
            code = data.get("code")
            artifacts.append(
                Artifact(
                    type="score_breakdown",
                    title=f"{city} ({code}) Risk Breakdown",
                    data=data,
                )
            )
        elif res.tool_name == "get_weather_stats":
            city = data.get("city") or data.get("code")
            code = data.get("code")
            metric = str(data.get("metric") or "").replace("_", " ")
            artifacts.append(
                Artifact(
                    type="stat",
                    title=f"{city} ({code}) — {metric}",
                    data=data,
                )
            )
        elif res.tool_name == "get_active_alerts":
            hub_filter = data.get("hub_filter")
            title = f"Active Alerts — {hub_filter}" if hub_filter else "Active Weather Alerts"
            artifacts.append(
                Artifact(
                    type="alerts",
                    title=title,
                    data=data,
                )
            )
    return artifacts


def compact_tool_observation(result: ToolResult) -> str:
    """Serialize a ToolResult concisely for feeding into the next LLM step."""
    payload = {
        "ok": result.ok,
        "tool": result.tool_name,
        "summary": result.summary,
        "data": result.data,
        "caveats": result.caveats,
        "sources": result.sources,
        "data_window": result.data_window,
    }
    return json.dumps(payload, default=str)


def compact_memory_tool_line(name: str, arguments: dict[str, Any], result: ToolResult) -> str:
    """Create a one-line compact summary for session history replay (AGENTS.md §7.7)."""
    arg_parts = ", ".join(f"{k}={v}" for k, v in arguments.items() if v is not None)
    data = result.data or {}

    if not result.ok:
        outcome = result.summary
    elif name == "rank_hubs":
        items = data.get("items") or []
        outcome = ", ".join(f"{i['code']} {i['score']}" for i in items[:5])
    elif name == "compare_hubs":
        hubs = data.get("hubs") or []
        outcome = ", ".join(f"{h['code']} overall {h['overall_score']}" for h in hubs)
    elif name == "explain_hub_risk":
        outcome = f"{data.get('code')} WDRI {data.get('overall_score')} ({data.get('overall_tier')})"
    elif name == "get_weather_stats":
        outcome = (
            f"{data.get('code')} {data.get('metric')}={data.get('value')} "
            f"({data.get('percentage')}%)"
        )
    elif name == "get_active_alerts":
        outcome = f"{data.get('total_alerts', 0)} active alert(s)"
    else:
        outcome = result.summary

    return f"[tool {name}({arg_parts}) -> {outcome}]"
