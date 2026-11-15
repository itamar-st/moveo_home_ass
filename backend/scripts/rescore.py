"""CLI script to compute and persist WDRI risk snapshots for all hubs.

Usage:
    python -m scripts.rescore
    python -m scripts.rescore --skip-nws
"""

import argparse
import asyncio

from app.config import get_settings
from app.db.session import get_session_maker
from app.logging import configure_logging
from app.scoring.runner import rescore_all_hubs


async def _run_cli(fetch_nws: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    session_maker = get_session_maker()
    async with session_maker() as session:
        summary = await rescore_all_hubs(session, fetch_nws=fetch_nws)
        print(
            f"rescore: hubs_scored={summary['hubs_scored']} "
            f"computed_at={summary['computed_at']} "
            f"nws_failures={summary['nws_failures']}"
        )


def main() -> None:
    """CLI entrypoint for rescoring all hubs."""
    parser = argparse.ArgumentParser(description="Compute and persist WDRI risk snapshots.")
    parser.add_argument(
        "--skip-nws",
        action="store_true",
        help="Skip live NWS alerts fetch and retain previous non-simulated alerts.",
    )
    args = parser.parse_args()
    asyncio.run(_run_cli(fetch_nws=not args.skip_nws))


if __name__ == "__main__":
    main()
