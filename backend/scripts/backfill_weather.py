"""Backfill 5 full calendar years + YTD of Open-Meteo daily weather for all hubs.

Usage:
    python -m scripts.backfill_weather [--years 5]
"""

import argparse
import asyncio
from datetime import date, timedelta

from app.config import get_settings
from app.db.session import get_session_maker
from app.ingestion.pipeline import refresh_weather_all_hubs
from app.logging import configure_logging


def compute_backfill_window(years: int, *, today: date | None = None) -> tuple[date, date]:
    """Compute `[start_date, end_date]` covering `years` full calendar years + YTD."""
    ref_today = today or date.today()
    start_date = date(ref_today.year - years, 1, 1)
    end_date = ref_today - timedelta(days=1)
    if end_date < start_date:
        end_date = ref_today
    return start_date, end_date


async def _run_cli(years: int) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    start_date, end_date = compute_backfill_window(years)
    session_maker = get_session_maker()
    async with session_maker() as session:
        summary = await refresh_weather_all_hubs(
            session,
            start_date=start_date,
            end_date=end_date,
        )
        print(
            f"backfill_weather: hubs={summary['hub_count']} "
            f"rows_upserted={summary['rows_upserted']} "
            f"window={summary['start_date']}..{summary['end_date']} "
            f"errors={len(summary['errors'])}"
        )


def main() -> None:
    """CLI entrypoint for weather backfill."""
    parser = argparse.ArgumentParser(
        description="Backfill historical Open-Meteo daily weather for all hubs."
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Number of full prior calendar years to include before YTD (default: 5).",
    )
    args = parser.parse_args()
    if args.years < 0:
        raise SystemExit("--years must be >= 0")
    asyncio.run(_run_cli(years=args.years))


if __name__ == "__main__":
    main()
