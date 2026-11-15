"""Deterministic Weather Disruption Risk Index (WDRI) scoring package."""

from app.scoring.config import (
    DEFAULT_SCORING_CONFIG,
    SCORING_VERSION,
    ScoringConfig,
    get_scoring_metadata,
    tier_for_score,
)
from app.scoring.engine import HubScoreResult, score_hub
from app.scoring.metrics import (
    AnnualMetrics,
    PeriodStatResult,
    climatology_window,
    compute_annual_metrics,
    compute_period_stat,
    resolve_period,
)

__all__ = [
    "DEFAULT_SCORING_CONFIG",
    "SCORING_VERSION",
    "AnnualMetrics",
    "HubScoreResult",
    "PeriodStatResult",
    "ScoringConfig",
    "climatology_window",
    "compute_annual_metrics",
    "compute_period_stat",
    "get_scoring_metadata",
    "resolve_period",
    "score_hub",
    "tier_for_score",
]
