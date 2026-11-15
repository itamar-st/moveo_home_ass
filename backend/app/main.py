"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.alerts.broadcaster import alert_broadcaster
from app.api.deps import APIError, SlidingWindowRateLimiter
from app.api.routes_admin import router as admin_router
from app.api.routes_alerts import router as alerts_router
from app.api.routes_chat import router as chat_router
from app.api.routes_health import router as health_router
from app.api.routes_hubs import router as hubs_router
from app.config import get_settings
from app.logging import configure_logging
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup/shutdown of the in-process APScheduler and background tasks."""
    settings = get_settings()
    start_scheduler(app, settings)
    try:
        yield
    finally:
        stop_scheduler(app)
        tasks = list(getattr(app.state, "background_tasks", ()))
        for task in tasks:
            task.cancel()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Weather Risk Intelligence Agent",
        version="0.1.0",
        description="AI-powered weather disruption risk analyst for US logistics hubs.",
        lifespan=lifespan,
    )

    app.state.scheduler_running = False
    app.state.alert_broadcaster = alert_broadcaster
    app.state.chat_rate_limiter = SlidingWindowRateLimiter(settings.chat_rate_limit_per_min)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        headers: dict[str, str] = {}
        if (
            exc.status_code == 429
            and isinstance(exc.details, dict)
            and exc.details.get("retry_after_seconds") is not None
        ):
            headers["Retry-After"] = str(exc.details["retry_after_seconds"])
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    app.include_router(health_router, prefix="/api")
    app.include_router(hubs_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(alerts_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")

    return app


app = create_app()
