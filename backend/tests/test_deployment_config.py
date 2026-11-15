"""Tests for M9 deployment configuration, URL normalization, and SSE headers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.loop import AgentEvent
from app.config import Settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_database_url_normalizes_railway_mysql_scheme() -> None:
    """Railway provides mysql:// URLs; Settings must map them to mysql+asyncmy://."""
    s1 = Settings(database_url="mysql://root:secret@mysql.railway.internal:3306/railway")
    assert s1.database_url == "mysql+asyncmy://root:secret@mysql.railway.internal:3306/railway"

    s2 = Settings(database_url="mysql+pymysql://user:pass@host:3306/db")
    assert s2.database_url == "mysql+asyncmy://user:pass@host:3306/db"

    s3 = Settings(database_url="mysql+asyncmy://wria:wria@localhost:3306/wria")
    assert s3.database_url == "mysql+asyncmy://wria:wria@localhost:3306/wria"

    s4 = Settings(database_url="sqlite+aiosqlite:///./test.db")
    assert s4.database_url == "sqlite+aiosqlite:///./test.db"


def test_cors_origins_list_parses_comma_separated_origins() -> None:
    """CORS_ORIGINS should split cleanly for local + deployed frontend domains."""
    settings = Settings(
        cors_origins="http://localhost:5173, https://moveo-home-ass-ist.up.railway.app , https://app.vercel.app"
    )
    assert settings.cors_origins_list == [
        "http://localhost:5173",
        "https://moveo-home-ass-ist.up.railway.app",
        "https://app.vercel.app",
    ]


def test_backend_dockerfile_runs_migrations_seed_and_uvicorn() -> None:
    """Backend Dockerfile CMD must migrate, idempotently seed, and bind $PORT."""
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "alembic upgrade head" in dockerfile
    assert "python -m scripts.seed --if-empty" in dockerfile
    assert "uvicorn app.main:app" in dockerfile
    assert "${PORT:-8000}" in dockerfile


def test_frontend_vercel_json_has_spa_rewrite() -> None:
    """Vercel config must rewrite all routes to /index.html for SPA routing."""
    vercel_path = REPO_ROOT / "frontend" / "vercel.json"
    assert vercel_path.exists()
    data = json.loads(vercel_path.read_text(encoding="utf-8"))
    assert data["rewrites"] == [{"source": "/(.*)", "destination": "/index.html"}]


def test_frontend_dockerfile_serves_spa_dist() -> None:
    """Frontend Dockerfile must build dist and serve it in SPA mode."""
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "npm run build" in dockerfile
    assert "serve -s dist" in dockerfile
    assert "${PORT:-3000}" in dockerfile


@pytest.mark.asyncio
async def test_chat_stream_sets_no_buffering_header(db_session) -> None:
    """SSE chat stream must send X-Accel-Buffering: no for reverse proxies."""
    from app.api.deps import get_db

    app = create_app()

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    async def _fake_run_agent(*args, **kwargs):
        yield AgentEvent(
            event="session",
            data={"session_id": "00000000-0000-0000-0000-000000000001", "user_message_id": 1},
        )
        yield AgentEvent(
            event="final",
            data={
                "message_id": 2,
                "answer": {
                    "answer_markdown": "Test answer",
                    "intent": "general",
                    "hubs": [],
                    "key_points": ["ok"],
                    "reasoning": ["test"],
                    "assumptions": [],
                    "confidence": "high",
                    "confidence_rationale": "test",
                    "follow_ups": [],
                },
                "artifacts": [],
                "model": "test-model",
                "latency_ms": 10,
                "warnings": [],
            },
        )
        yield AgentEvent(event="done", data={})

    with patch("app.api.routes_chat.run_agent", side_effect=_fake_run_agent):
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
            client.stream(
                "POST",
                "/api/chat/stream",
                json={"message": "hello"},
            ) as response,
        ):
            assert response.status_code == 200
            assert response.headers.get("x-accel-buffering") == "no"
            assert response.headers.get("cache-control") == "no-cache"


@pytest.mark.asyncio
async def test_alerts_stream_sets_no_buffering_header(db_session) -> None:
    """SSE alerts stream must send X-Accel-Buffering: no for reverse proxies."""
    from app.api.deps import get_db
    from app.db.models import Base

    conn = await db_session.connection()
    await conn.run_sync(Base.metadata.create_all)

    app = create_app()

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    with patch("starlette.requests.Request.is_disconnected", new=AsyncMock(return_value=True)):
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
            client.stream("GET", "/api/alerts/stream") as response,
        ):
            assert response.status_code == 200
            assert response.headers.get("x-accel-buffering") == "no"
            assert response.headers.get("cache-control") == "no-cache"
            body = (await response.aread()).decode("utf-8")
            assert "hello" in body
