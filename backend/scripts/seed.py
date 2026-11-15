"""Idempotent hub seeding script.

Usage:
    python -m scripts.seed
    python -m scripts.seed --if-empty
"""

import argparse
import asyncio
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub
from app.db.session import get_session_maker
from app.ingestion.fema_nri import load_nri_data

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"
DEFAULT_HUBS_PATH = DATA_DIR / "hubs.json"
DEFAULT_NRI_PATH = DATA_DIR / "nri_hubs.json"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


async def seed_hubs(
    session: AsyncSession,
    *,
    if_empty: bool = False,
    hubs_path: Path | None = None,
    nri_path: Path | None = None,
) -> dict[str, int | bool]:
    """Seed the 20 distribution hubs into the `hubs` table idempotently.

    If `nri_hubs.json` exists, also populates `hub.nri` keyed by hub code or county_fips.
    """
    if if_empty:
        existing_count = await session.scalar(select(func.count()).select_from(Hub))
        if existing_count and existing_count > 0:
            logger.info("hubs table already has %d rows; skipping (--if-empty)", existing_count)
            return {"inserted": 0, "updated": 0, "skipped": True}

    resolved_hubs_path = hubs_path or DEFAULT_HUBS_PATH
    resolved_nri_path = nri_path or DEFAULT_NRI_PATH

    hub_entries: list[dict[str, Any]] = _load_json(resolved_hubs_path)
    nri_data: dict[str, Any] = load_nri_data(resolved_nri_path)

    existing_result = await session.execute(select(Hub))
    existing_by_code = {hub.code: hub for hub in existing_result.scalars().all()}

    inserted = 0
    updated = 0

    for entry in hub_entries:
        code = entry["code"]
        county_fips = entry["county_fips"]
        nri_payload = nri_data.get(code) or nri_data.get(county_fips)

        if code in existing_by_code:
            hub = existing_by_code[code]
            hub.city = entry["city"]
            hub.state = entry["state"]
            hub.region = entry["region"]
            hub.latitude = Decimal(str(entry["latitude"]))
            hub.longitude = Decimal(str(entry["longitude"]))
            hub.county_fips = county_fips
            hub.timezone = entry["timezone"]
            hub.aliases = entry.get("aliases")
            if nri_payload is not None:
                hub.nri = nri_payload
            updated += 1
        else:
            hub = Hub(
                code=code,
                city=entry["city"],
                state=entry["state"],
                region=entry["region"],
                latitude=Decimal(str(entry["latitude"])),
                longitude=Decimal(str(entry["longitude"])),
                county_fips=county_fips,
                timezone=entry["timezone"],
                aliases=entry.get("aliases"),
                nri=nri_payload,
            )
            session.add(hub)
            inserted += 1

    await session.commit()
    logger.info("Seeded hubs: inserted=%d updated=%d", inserted, updated)
    return {"inserted": inserted, "updated": updated, "skipped": False}


async def _run_cli(if_empty: bool) -> None:
    session_maker = get_session_maker()
    async with session_maker() as session:
        result = await seed_hubs(session, if_empty=if_empty)
        print(
            f"seed_hubs: inserted={result['inserted']} "
            f"updated={result['updated']} skipped={result['skipped']}"
        )


def main() -> None:
    """CLI entrypoint for seeding hubs."""
    parser = argparse.ArgumentParser(description="Seed distribution hubs into the database.")
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Skip seeding if the hubs table already contains rows.",
    )
    args = parser.parse_args()
    asyncio.run(_run_cli(if_empty=args.if_empty))


if __name__ == "__main__":
    main()
