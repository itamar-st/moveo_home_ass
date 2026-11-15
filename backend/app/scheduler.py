"""APScheduler background jobs for periodic NWS alert refresh and daily weather rescoring."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from app.alerts.service import run_rescore_and_alerts
from app.config import Settings, get_settings
from app.db.session import get_session_maker

logger = logging.getLogger(__name__)


async def scheduled_nws_rescore() -> None:
    """Periodic job: refresh live NWS alerts and rescore all hubs."""
    session_maker = get_session_maker()
    try:
        async with session_maker() as session:
            await run_rescore_and_alerts(session, refresh_nws=True, refresh_weather=False)
    except Exception:
        logger.exception("scheduled_nws_rescore_failed")


async def scheduled_daily_weather_rescore() -> None:
    """Daily job: refresh Open-Meteo weather data and rescore all hubs."""
    session_maker = get_session_maker()
    try:
        async with session_maker() as session:
            await run_rescore_and_alerts(session, refresh_nws=True, refresh_weather=True)
    except Exception:
        logger.exception("scheduled_daily_weather_rescore_failed")


def build_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    """Create and configure the AsyncIOScheduler with NWS and daily weather jobs."""
    cfg = settings or get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        scheduled_nws_rescore,
        trigger=IntervalTrigger(minutes=max(1, cfg.nws_refresh_minutes)),
        id="nws_rescore",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        scheduled_daily_weather_rescore,
        trigger=CronTrigger.from_crontab(cfg.weather_refresh_cron, timezone="UTC"),
        id="daily_weather_rescore",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler


def start_scheduler(app: FastAPI, settings: Settings | None = None) -> AsyncIOScheduler | None:
    """Start the in-process scheduler if `SCHEDULER_ENABLED` is true."""
    cfg = settings or get_settings()
    if not cfg.scheduler_enabled:
        app.state.scheduler = None
        app.state.scheduler_running = False
        logger.info("scheduler_disabled")
        return None

    scheduler = build_scheduler(cfg)
    scheduler.start()
    app.state.scheduler = scheduler
    app.state.scheduler_running = True
    logger.info(
        "scheduler_started nws_refresh_minutes=%d weather_refresh_cron=%s",
        cfg.nws_refresh_minutes,
        cfg.weather_refresh_cron,
    )
    return scheduler


def stop_scheduler(app: FastAPI) -> None:
    """Shut down the in-process scheduler cleanly on app shutdown."""
    scheduler: AsyncIOScheduler | None = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        app.state.scheduler = None
    app.state.scheduler_running = False
    logger.info("scheduler_stopped")
