"""FEMA National Risk Index (NRI) data loader and hub updater."""

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub

logger = logging.getLogger(__name__)

DEFAULT_NRI_PATH = Path(__file__).resolve().parent.parent / "data" / "nri_hubs.json"


def load_nri_data(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load pre-extracted FEMA NRI county scores from JSON.

    Returns an empty dict if the file does not exist yet.
    """
    resolved = path or DEFAULT_NRI_PATH
    if not resolved.exists():
        logger.warning("NRI data file not found at %s", resolved)
        return {}

    with resolved.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise TypeError(f"Expected JSON object in {resolved}, got {type(raw).__name__}")
    return raw


async def apply_nri_to_hubs(
    session: AsyncSession,
    *,
    nri_data: dict[str, dict[str, Any]] | None = None,
    path: Path | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Populate `hubs.nri` from `nri_hubs.json` (keyed by hub code or county_fips)."""
    resolved_data = nri_data if nri_data is not None else load_nri_data(path)
    result = await session.execute(select(Hub).order_by(Hub.code))
    hubs = result.scalars().all()

    updated = 0
    missing = 0
    for hub in hubs:
        payload = resolved_data.get(hub.code) or resolved_data.get(hub.county_fips)
        if payload is not None:
            hub.nri = payload
            updated += 1
        else:
            missing += 1
            logger.warning(
                "No NRI entry found for hub=%s county_fips=%s",
                hub.code,
                hub.county_fips,
            )

    if commit:
        await session.commit()

    logger.info("Applied FEMA NRI data: updated=%d missing=%d", updated, missing)
    return {"updated": updated, "missing": missing}
