"""Pure deterministic WDRI scoring engine (no database or network I/O)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.scoring.config import (
    DEFAULT_SCORING_CONFIG,
    HAZARD_NAMES,
    REQUIRED_NRI_KEYS,
    ScoringConfig,
    tier_for_score,
)
from app.scoring.metrics import AnnualMetrics, climatology_window, compute_annual_metrics


@dataclass(frozen=True)
class HubScoreResult:
    """Complete deterministic scoring result for one hub."""

    overall_score: float
    overall_tier: str
    operational_score: float
    operational_tier: str
    live_bump: float
    confidence: str
    details: dict[str, Any]


def normalize_metric(value: float, cap: float) -> float:
    """Normalize an annual metric against a fixed cap onto [0, 100]."""
    if cap <= 0:
        raise ValueError("cap must be > 0")
    if value <= 0:
        return 0.0
    return min(value / cap, 1.0) * 100.0


def _extract_nri_scores(
    nri_payload: Mapping[str, Any] | None,
) -> tuple[dict[str, float], list[str], list[str]]:
    """Extract NRI scores into floats, tracking missing keys and Not-Applicable nulls."""
    if not nri_payload or not isinstance(nri_payload.get("scores"), Mapping):
        return (
            {k: 0.0 for k in REQUIRED_NRI_KEYS},
            list(REQUIRED_NRI_KEYS),
            [],
        )

    raw_scores = nri_payload["scores"]
    resolved: dict[str, float] = {}
    missing_keys: list[str] = []
    na_keys: list[str] = []

    for key in REQUIRED_NRI_KEYS:
        if key not in raw_scores:
            missing_keys.append(key)
            resolved[key] = 0.0
        elif raw_scores[key] is None:
            na_keys.append(key)
            resolved[key] = 0.0
        else:
            resolved[key] = max(0.0, min(100.0, float(raw_scores[key])))

    return resolved, missing_keys, na_keys


def _compute_confidence(coverage_pct: float, missing_nri_count: int) -> str:
    """Determine hub score confidence per AGENTS.md §6.4."""
    if coverage_pct >= 95.0 and missing_nri_count == 0:
        return "high"
    if coverage_pct >= 80.0 and missing_nri_count <= 1:
        return "medium"
    return "low"


def _build_drivers(
    hazard_scores: dict[str, float],
    contributions: dict[str, float],
    metrics: AnnualMetrics,
    nri: dict[str, float],
) -> list[str]:
    """Generate human-readable risk drivers ordered by contribution descending."""
    ordered_hazards = sorted(
        HAZARD_NAMES,
        key=lambda h: (contributions[h], hazard_scores[h]),
        reverse=True,
    )

    drivers: list[str] = []
    for hazard in ordered_hazards:
        score = hazard_scores[hazard]
        if score <= 0.0:
            continue
        if hazard == "winter":
            drivers.append(
                f"winter: {metrics.sig_snow_days:.1f} sig snow days/yr, "
                f"{metrics.hard_freeze_days:.1f} freeze days/yr, "
                f"winter NRI {nri['winter_weather']:.1f}, "
                f"ice_storm NRI {nri['ice_storm']:.1f} (score {score:.1f})"
            )
        elif hazard == "hurricane":
            drivers.append(
                f"hurricane: hurricane NRI {nri['hurricane']:.1f}, "
                f"{metrics.high_wind_days:.1f} high-wind days/yr (score {score:.1f})"
            )
        elif hazard == "flood":
            flood_nri = max(nri["riverine_flood"], nri["coastal_flood"])
            drivers.append(
                f"flood: {metrics.heavy_precip_days:.1f} heavy-precip days/yr, "
                f"flood NRI {flood_nri:.1f} (score {score:.1f})"
            )
        elif hazard == "severe_storm":
            drivers.append(
                f"severe_storm: tornado NRI {nri['tornado']:.1f}, "
                f"hail NRI {nri['hail']:.1f}, strong_wind NRI {nri['strong_wind']:.1f} "
                f"(score {score:.1f})"
            )
        elif hazard == "heat":
            drivers.append(
                f"heat: {metrics.extreme_heat_days:.1f} days >= 37.8 C/yr, "
                f"heat_wave NRI {nri['heat_wave']:.1f} (score {score:.1f})"
            )
    return drivers


def score_hub(
    daily_rows: Sequence[Mapping[str, Any] | Any],
    nri_payload: Mapping[str, Any] | None,
    active_alerts: Sequence[Mapping[str, Any]] = (),
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    reference_date: date | None = None,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
    extra_caveats: Sequence[str] = (),
) -> HubScoreResult:
    """Compute deterministic WDRI structural and operational scores for a single hub."""
    if start_date is None or end_date is None:
        default_start, default_end = climatology_window(
            reference_date, years=config.climatology_years
        )
        resolved_start = start_date or default_start
        resolved_end = end_date or default_end
    else:
        resolved_start = start_date
        resolved_end = end_date

    metrics = compute_annual_metrics(
        daily_rows,
        start_date=resolved_start,
        end_date=resolved_end,
        config=config,
    )
    nri, missing_nri, na_nri = _extract_nri_scores(nri_payload)

    # Normalized observed components (0-100)
    norm_snow = normalize_metric(metrics.sig_snow_days, config.caps["sig_snow_days"])
    norm_freeze = normalize_metric(metrics.hard_freeze_days, config.caps["hard_freeze_days"])
    norm_fzrn = normalize_metric(metrics.freezing_rain_days, config.caps["freezing_rain_days"])
    norm_precip = normalize_metric(metrics.heavy_precip_days, config.caps["heavy_precip_days"])
    norm_wind = normalize_metric(metrics.high_wind_days, config.caps["high_wind_days"])
    norm_heat = normalize_metric(metrics.extreme_heat_days, config.caps["extreme_heat_days"])

    # Hazard scores (AGENTS.md §6.3)
    obs_winter = 0.5 * norm_snow + 0.3 * norm_freeze + 0.2 * norm_fzrn
    nri_winter = (
        0.5 * nri["winter_weather"] + 0.3 * nri["ice_storm"] + 0.2 * nri["cold_wave"]
    )
    winter_score = round(0.6 * obs_winter + 0.4 * nri_winter, 2)

    hurricane_score = round(0.8 * nri["hurricane"] + 0.2 * norm_wind, 2)

    flood_nri = max(nri["riverine_flood"], nri["coastal_flood"])
    flood_score = round(0.5 * norm_precip + 0.5 * flood_nri, 2)

    nri_severe = 0.4 * nri["tornado"] + 0.3 * nri["hail"] + 0.3 * nri["strong_wind"]
    severe_storm_score = round(0.8 * nri_severe + 0.2 * norm_wind, 2)

    heat_score = round(0.6 * norm_heat + 0.4 * nri["heat_wave"], 2)

    hazard_scores: dict[str, float] = {
        "winter": winter_score,
        "flood": flood_score,
        "hurricane": hurricane_score,
        "severe_storm": severe_storm_score,
        "heat": heat_score,
    }

    # Overall structural score (WDRI, §6.4)
    weighted_sum = sum(
        config.hazard_weights[h] * hazard_scores[h] for h in HAZARD_NAMES
    )
    top_hazard = max(HAZARD_NAMES, key=lambda h: (hazard_scores[h], config.hazard_weights[h]))
    max_hazard_score = hazard_scores[top_hazard]

    overall_score = round(
        config.wdri_weighted_factor * weighted_sum
        + config.wdri_max_factor * max_hazard_score,
        2,
    )

    # Contributions to WDRI (0.7 * w_h * score_h, plus 0.3 * max attributed to top hazard)
    contributions: dict[str, float] = {}
    for h in HAZARD_NAMES:
        contrib = config.wdri_weighted_factor * config.hazard_weights[h] * hazard_scores[h]
        if h == top_hazard:
            contrib += config.wdri_max_factor * max_hazard_score
        contributions[h] = round(contrib, 2)

    # Operational overlay (§6.5)
    serialized_alerts = [dict(a) for a in active_alerts]
    live_bump = max(
        (config.alert_bumps.get(str(a.get("severity", "")), 0.0) for a in serialized_alerts),
        default=0.0,
    )
    operational_score = round(min(100.0, overall_score + live_bump), 2)

    confidence = _compute_confidence(metrics.coverage_pct, len(missing_nri))
    drivers = _build_drivers(hazard_scores, contributions, metrics, nri)

    caveats: list[str] = [
        "Observed metrics use Open-Meteo ERA5 reanalysis (~9-25 km grid), not station observations.",
    ]
    if metrics.coverage_pct < 95.0:
        caveats.append(
            f"Weather data coverage in window is {metrics.coverage_pct:.1f}% (< 95%)."
        )
    if missing_nri:
        caveats.append(
            f"Missing FEMA NRI fields (treated as 0): {', '.join(missing_nri)}."
        )
    if na_nri:
        caveats.append(
            f"FEMA NRI fields not applicable for county (treated as 0): {', '.join(na_nri)}."
        )
    for c in extra_caveats:
        if c not in caveats:
            caveats.append(c)

    hazards_detail = {
        "winter": {
            "score": winter_score,
            "contribution": contributions["winter"],
            "metrics": {
                "sig_snow_days": metrics.sig_snow_days,
                "hard_freeze_days": metrics.hard_freeze_days,
                "freezing_rain_days": metrics.freezing_rain_days,
            },
            "nri": {
                "winter_weather": nri["winter_weather"],
                "ice_storm": nri["ice_storm"],
                "cold_wave": nri["cold_wave"],
            },
        },
        "hurricane": {
            "score": hurricane_score,
            "contribution": contributions["hurricane"],
            "metrics": {"high_wind_days": metrics.high_wind_days},
            "nri": {"hurricane": nri["hurricane"]},
        },
        "flood": {
            "score": flood_score,
            "contribution": contributions["flood"],
            "metrics": {"heavy_precip_days": metrics.heavy_precip_days},
            "nri": {
                "riverine_flood": nri["riverine_flood"],
                "coastal_flood": nri["coastal_flood"],
            },
        },
        "severe_storm": {
            "score": severe_storm_score,
            "contribution": contributions["severe_storm"],
            "metrics": {"high_wind_days": metrics.high_wind_days},
            "nri": {
                "tornado": nri["tornado"],
                "hail": nri["hail"],
                "strong_wind": nri["strong_wind"],
            },
        },
        "heat": {
            "score": heat_score,
            "contribution": contributions["heat"],
            "metrics": {"extreme_heat_days": metrics.extreme_heat_days},
            "nri": {"heat_wave": nri["heat_wave"]},
        },
    }

    details: dict[str, Any] = {
        "scoring_version": config.scoring_version,
        "window": [resolved_start.isoformat(), resolved_end.isoformat()],
        "coverage_pct": metrics.coverage_pct,
        "confidence": confidence,
        "hazards": hazards_detail,
        "drivers": drivers,
        "live_bump": live_bump,
        "active_alerts": serialized_alerts,
        "caveats": caveats,
    }

    return HubScoreResult(
        overall_score=overall_score,
        overall_tier=tier_for_score(overall_score),
        operational_score=operational_score,
        operational_tier=tier_for_score(operational_score),
        live_bump=live_bump,
        confidence=confidence,
        details=details,
    )
