"""REST endpoints for hubs, risk rankings, snapshot history, and scoring metadata."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import APIError, get_db
from app.config import AVAILABLE_LLM_MODELS, get_settings
from app.db.models import Hub, RiskSnapshot
from app.db.repositories import (
    get_hub_by_code,
    get_hub_snapshot_history,
    get_latest_snapshot_for_hub,
    list_hubs_with_latest_snapshot,
)
from app.schemas.api import (
    HazardFilter,
    HubDetailOut,
    HubOut,
    LLMModelOption,
    ModelsMetaResponse,
    RankingItemOut,
    RankingsResponse,
    RegionName,
    ScoreKind,
    ScoringMetaResponse,
    SnapshotSummaryOut,
)
from app.scoring.config import SCORING_VERSION, get_scoring_metadata, tier_for_score

router = APIRouter(tags=["hubs"])


def _hub_to_summary(hub: Hub, snap: RiskSnapshot | None) -> HubOut:
    """Map ORM Hub + optional latest RiskSnapshot to HubOut."""
    overall = float(snap.overall_score) if snap is not None else None
    operational = float(snap.operational_score) if snap is not None else None
    return HubOut(
        code=hub.code,
        name=f"{hub.city} Hub",
        city=hub.city,
        state=hub.state,
        region=hub.region,  # type: ignore[arg-type]
        latitude=float(hub.latitude),
        longitude=float(hub.longitude),
        county_fips=hub.county_fips,
        overall_score=overall,
        overall_tier=tier_for_score(overall) if overall is not None else None,
        operational_score=operational,
        operational_tier=tier_for_score(operational) if operational is not None else None,
    )


def _hazard_top_driver(hazard: str, details: dict[str, Any]) -> str | None:
    """Build a concise top-driver label for a ranking item."""
    if hazard == "overall":
        drivers = details.get("drivers") or []
        return str(drivers[0]) if drivers else None

    hazards = details.get("hazards") or {}
    h_detail = hazards.get(hazard) or {}
    metrics = h_detail.get("metrics") or {}
    nri = h_detail.get("nri") or {}

    if hazard == "winter":
        return (
            f"sig_snow_days: {metrics.get('sig_snow_days', 0.0)}/yr, "
            f"winter NRI {nri.get('winter_weather', 0.0)}"
        )
    if hazard == "hurricane":
        return (
            f"hurricane NRI: {nri.get('hurricane', 0.0)}, "
            f"high_wind_days: {metrics.get('high_wind_days', 0.0)}/yr"
        )
    if hazard == "flood":
        flood_nri = max(nri.get("riverine_flood", 0.0), nri.get("coastal_flood", 0.0))
        return (
            f"heavy_precip_days: {metrics.get('heavy_precip_days', 0.0)}/yr, "
            f"flood NRI {flood_nri}"
        )
    if hazard == "severe_storm":
        return (
            f"tornado NRI: {nri.get('tornado', 0.0)}, "
            f"hail NRI: {nri.get('hail', 0.0)}"
        )
    if hazard == "heat":
        return (
            f"extreme_heat_days: {metrics.get('extreme_heat_days', 0.0)}/yr, "
            f"heat_wave NRI {nri.get('heat_wave', 0.0)}"
        )
    return None


@router.get("/hubs", response_model=list[HubOut])
async def list_hubs(
    db: Annotated[AsyncSession, Depends(get_db)],
    region: RegionName | None = None,
) -> list[HubOut]:
    """List all hubs (optionally filtered by region) with latest risk scores."""
    pairs = await list_hubs_with_latest_snapshot(db, region=region)
    return [_hub_to_summary(hub, snap) for hub, snap in pairs]


@router.get("/hubs/{code}", response_model=HubDetailOut)
async def get_hub_detail(
    code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HubDetailOut:
    """Return full details, FEMA NRI scores, and latest snapshot breakdown for one hub."""
    hub = await get_hub_by_code(db, code)
    if hub is None:
        raise APIError(
            404,
            "hub_not_found",
            f"Hub '{code}' was not found.",
            details={"code": code},
        )
    snap = await get_latest_snapshot_for_hub(db, hub.id)
    summary = _hub_to_summary(hub, snap)
    return HubDetailOut(
        **summary.model_dump(),
        timezone=hub.timezone,
        aliases=list(hub.aliases or []),
        nri=hub.nri,
        computed_at=snap.computed_at if snap is not None else None,
        details=snap.details if snap is not None else None,
    )


@router.get("/hubs/{code}/history", response_model=list[SnapshotSummaryOut])
async def get_hub_history(
    code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> list[SnapshotSummaryOut]:
    """Return historical scoring snapshots for a hub, newest first."""
    hub = await get_hub_by_code(db, code)
    if hub is None:
        raise APIError(
            404,
            "hub_not_found",
            f"Hub '{code}' was not found.",
            details={"code": code},
        )
    snapshots = await get_hub_snapshot_history(db, hub.id, limit=limit)
    out: list[SnapshotSummaryOut] = []
    for snap in snapshots:
        overall = float(snap.overall_score)
        operational = float(snap.operational_score)
        details = snap.details or {}
        out.append(
            SnapshotSummaryOut(
                computed_at=snap.computed_at,
                overall_score=overall,
                overall_tier=tier_for_score(overall),
                operational_score=operational,
                operational_tier=tier_for_score(operational),
                live_bump=float(details.get("live_bump", 0.0)),
                confidence=details.get("confidence"),
            )
        )
    return out


@router.get("/rankings", response_model=RankingsResponse)
async def get_rankings(
    db: Annotated[AsyncSession, Depends(get_db)],
    hazard: HazardFilter = "overall",
    region: RegionName | None = None,
    score_kind: ScoreKind = "structural",
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> RankingsResponse:
    """Rank hubs by overall WDRI or a specific hazard score, with breakdowns."""
    pairs = await list_hubs_with_latest_snapshot(db, region=region)
    scored_pairs = [(hub, snap) for hub, snap in pairs if snap is not None]

    if not scored_pairs:
        return RankingsResponse(
            hazard=hazard,
            score_kind=score_kind,
            region=region,
            scoring_version=SCORING_VERSION,
            computed_at=None,
            items=[],
        )

    latest_computed_at: datetime = max(snap.computed_at for _, snap in scored_pairs)
    scoring_version = str(
        (scored_pairs[0][1].details or {}).get("scoring_version", SCORING_VERSION)
    )

    def _extract_sort_score(snap: RiskSnapshot) -> float:
        details = snap.details or {}
        if hazard == "overall":
            return (
                float(snap.operational_score)
                if score_kind == "operational"
                else float(snap.overall_score)
            )
        hazards_map = details.get("hazards") or {}
        h_info = hazards_map.get(hazard) or {}
        return float(h_info.get("score", 0.0))

    sorted_pairs = sorted(
        scored_pairs,
        key=lambda pair: (-_extract_sort_score(pair[1]), pair[0].code),
    )[:limit]

    items: list[RankingItemOut] = []
    for idx, (hub, snap) in enumerate(sorted_pairs, start=1):
        details = snap.details or {}
        hazards_map = details.get("hazards") or {}
        score = _extract_sort_score(snap)
        hazard_scores = {
            h: float(info.get("score", 0.0))
            for h, info in hazards_map.items()
            if isinstance(info, dict)
        }
        breakdown = (
            hazards_map
            if hazard == "overall"
            else dict(hazards_map.get(hazard) or {})
        )
        items.append(
            RankingItemOut(
                rank=idx,
                code=hub.code,
                name=f"{hub.city} Hub",
                city=hub.city,
                state=hub.state,
                region=hub.region,  # type: ignore[arg-type]
                score=score,
                tier=tier_for_score(score),
                top_driver=_hazard_top_driver(hazard, details),
                hazard_scores=hazard_scores,
                breakdown=breakdown,
            )
        )

    return RankingsResponse(
        hazard=hazard,
        score_kind=score_kind,
        region=region,
        scoring_version=scoring_version,
        computed_at=latest_computed_at,
        items=items,
    )


@router.get("/meta/scoring", response_model=ScoringMetaResponse)
async def get_scoring_meta() -> ScoringMetaResponse:
    """Expose WDRI v1 weights, thresholds, caps, tiers, data sources, and assumptions."""
    return ScoringMetaResponse(**get_scoring_metadata())


@router.get("/meta/models", response_model=ModelsMetaResponse)
async def get_models_meta() -> ModelsMetaResponse:
    """Expose the selectable LLM models and configured default."""
    settings = get_settings()
    return ModelsMetaResponse(
        default_model=settings.openrouter_model,
        models=[LLMModelOption(**item) for item in AVAILABLE_LLM_MODELS],
    )
