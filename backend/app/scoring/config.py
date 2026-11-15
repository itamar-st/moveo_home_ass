"""Versioned configuration, thresholds, caps, weights, and tiers for WDRI v1."""

from dataclasses import dataclass, field
from typing import Any, Literal

SCORING_VERSION = "wdri-v1"

HazardName = Literal["winter", "hurricane", "flood", "severe_storm", "heat"]
HAZARD_NAMES: tuple[HazardName, ...] = (
    "winter",
    "flood",
    "hurricane",
    "severe_storm",
    "heat",
)

REQUIRED_NRI_KEYS: tuple[str, ...] = (
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
)

ALERT_SEVERITY_BUMPS: dict[str, float] = {
    "Extreme": 30.0,
    "Severe": 20.0,
    "Moderate": 10.0,
    "Minor": 5.0,
}

TIER_BANDS: tuple[tuple[str, float, float], ...] = (
    ("Low", 0.0, 25.0),
    ("Moderate", 25.0, 50.0),
    ("High", 50.0, 75.0),
    ("Severe", 75.0, 100.0),
)


@dataclass(frozen=True)
class ScoringConfig:
    """Immutable configuration for deterministic WDRI v1 scoring."""

    scoring_version: str = SCORING_VERSION
    climatology_years: int = 5

    # Daily event thresholds
    snowfall_day_cm: float = 0.1
    sig_snow_cm: float = 2.5
    hard_freeze_c: float = -10.0
    freezing_rain_codes: frozenset[int] = frozenset({56, 57, 66, 67})
    heavy_precip_mm: float = 25.0
    high_wind_kmh: float = 80.0
    extreme_heat_c: float = 37.8

    # Annual frequency caps (value >= cap normalizes to 100), calibrated in M3
    caps: dict[str, float] = field(
        default_factory=lambda: {
            "sig_snow_days": 12.0,
            "hard_freeze_days": 25.0,
            "freezing_rain_days": 5.0,
            "heavy_precip_days": 16.0,
            "high_wind_days": 10.0,
            "extreme_heat_days": 30.0,
        }
    )

    # Top-level hazard weights in WDRI weighted average (sum to 1.0)
    hazard_weights: dict[str, float] = field(
        default_factory=lambda: {
            "winter": 0.25,
            "flood": 0.25,
            "hurricane": 0.20,
            "severe_storm": 0.20,
            "heat": 0.10,
        }
    )

    # WDRI blend: 0.7 * weighted + 0.3 * max(hazard_scores)
    wdri_weighted_factor: float = 0.70
    wdri_max_factor: float = 0.30

    # Within-hazard sub-weights (observed vs NRI)
    sub_weights: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "winter": {
                "observed_weight": 0.60,
                "nri_weight": 0.40,
                "observed": {
                    "sig_snow_days": 0.50,
                    "hard_freeze_days": 0.30,
                    "freezing_rain_days": 0.20,
                },
                "nri": {
                    "winter_weather": 0.50,
                    "ice_storm": 0.30,
                    "cold_wave": 0.20,
                },
            },
            "hurricane": {
                "nri_weight": 0.80,
                "observed_weight": 0.20,
                "nri": {"hurricane": 1.0},
                "observed": {"high_wind_days": 1.0},
            },
            "flood": {
                "observed_weight": 0.50,
                "nri_weight": 0.50,
                "observed": {"heavy_precip_days": 1.0},
                "nri": {"max_riverine_coastal": 1.0},
            },
            "severe_storm": {
                "nri_weight": 0.80,
                "observed_weight": 0.20,
                "nri": {
                    "tornado": 0.40,
                    "hail": 0.30,
                    "strong_wind": 0.30,
                },
                "observed": {"high_wind_days": 1.0},
            },
            "heat": {
                "observed_weight": 0.60,
                "nri_weight": 0.40,
                "observed": {"extreme_heat_days": 1.0},
                "nri": {"heat_wave": 1.0},
            },
        }
    )

    # Operational alert severity bumps
    alert_bumps: dict[str, float] = field(default_factory=lambda: dict(ALERT_SEVERITY_BUMPS))


DEFAULT_SCORING_CONFIG = ScoringConfig()


def tier_for_score(score: float) -> str:
    """Map a 0-100 risk score to its categorical tier."""
    if score < 25.0:
        return "Low"
    if score < 50.0:
        return "Moderate"
    if score < 75.0:
        return "High"
    return "Severe"


def get_scoring_metadata(config: ScoringConfig = DEFAULT_SCORING_CONFIG) -> dict[str, Any]:
    """Build the `/api/meta/scoring` payload describing WDRI methodology."""
    return {
        "scoring_version": config.scoring_version,
        "weights": {
            "hazards": dict(config.hazard_weights),
            "wdri_blend": {
                "weighted_average": config.wdri_weighted_factor,
                "max_hazard": config.wdri_max_factor,
            },
        },
        "sub_weights": config.sub_weights,
        "thresholds": {
            "snowfall_day_cm": config.snowfall_day_cm,
            "sig_snow_cm": config.sig_snow_cm,
            "hard_freeze_c": config.hard_freeze_c,
            "freezing_rain_weather_codes": sorted(config.freezing_rain_codes),
            "heavy_precip_mm": config.heavy_precip_mm,
            "high_wind_kmh": config.high_wind_kmh,
            "extreme_heat_c": config.extreme_heat_c,
        },
        "caps": dict(config.caps),
        "tiers": [
            {"name": name, "min_inclusive": low, "max_exclusive": high if name != "Severe" else None}
            for name, low, high in TIER_BANDS
        ],
        "alert_bumps": dict(config.alert_bumps),
        "data_sources": [
            {
                "name": "Open-Meteo Historical Weather API (ERA5/ERA5-Land reanalysis)",
                "window": f"Last {config.climatology_years} full calendar years",
                "fields": [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "snowfall_sum",
                    "wind_gusts_10m_max",
                    "weather_code",
                ],
            },
            {
                "name": "FEMA National Risk Index (County-level *_RISKS national percentiles)",
                "fields": list(REQUIRED_NRI_KEYS),
            },
            {
                "name": "National Weather Service (NWS) Active Alerts API",
                "role": "Operational overlay only (never alters structural WDRI ranking)",
            },
        ],
        "assumptions": [
            "Observed climatology uses the last 5 full calendar years of Open-Meteo daily reanalysis data.",
            "Snowfall statistic days use >= 0.1 cm; structural winter scoring uses significant snow days >= 2.5 cm (~1 inch).",
            "Fixed normalization caps prevent a hub's score from shifting when other hubs are added or removed.",
            "NRI fields marked Not Applicable (null) or missing are treated as 0 and surfaced in caveats.",
            "Live NWS alerts apply a temporary operational bump (capped at 100) without modifying structural scores.",
        ],
    }
