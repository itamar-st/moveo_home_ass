"""Integration bridge to M3 scoring configuration and snapshot repositories."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub, RiskSnapshot
from app.db.repositories import list_hubs_with_latest_snapshot
from app.scoring.config import (
    DEFAULT_SCORING_CONFIG,
    SCORING_VERSION,
    get_scoring_metadata,
    tier_for_score,
)


def get_scoring_version() -> str:
    """Return active scoring version string from M3 scoring config."""
    return SCORING_VERSION


def score_to_tier(score: float) -> str:
    """Map a 0-100 score to Low / Moderate / High / Severe using M3 tier_for_score."""
    return tier_for_score(score)


def get_scoring_weights_summary() -> dict[str, Any]:
    """Return deterministic WDRI weights, thresholds, caps, and tier cutoffs from M3."""
    meta = get_scoring_metadata(DEFAULT_SCORING_CONFIG)
    return {
        "scoring_version": meta["scoring_version"],
        "overall_formula": "0.7 * weighted_sum + 0.3 * max(hazard_scores)",
        "hazard_weights": meta["weights"]["hazards"],
        "structural_blend": meta["weights"]["wdri_blend"],
        "sub_weights": meta["sub_weights"],
        "caps": meta["caps"],
        "thresholds": meta["thresholds"],
        "tiers": {
            "Low": "< 25",
            "Moderate": "25 to < 50",
            "High": "50 to < 75",
            "Severe": ">= 75",
        },
    }


async def load_latest_snapshots_by_hub(
    db: AsyncSession,
    *,
    region: str | None = None,
) -> list[tuple[Hub, RiskSnapshot | None]]:
    """Delegate to M3's repository helper for hubs paired with latest RiskSnapshot."""
    return await list_hubs_with_latest_snapshot(db, region=region)
