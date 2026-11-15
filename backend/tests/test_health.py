"""Tests for health endpoint and application settings."""

from collections.abc import AsyncGenerator

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db.session import get_db
from app.main import create_app


async def test_health_ok(client: AsyncClient) -> None:
    """GET /api/health returns 200 and db='ok' when the DB ping succeeds."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert data["scoring_version"] == "wdri_v1"
    assert data["last_scored_at"] is None
    assert data["weather_data_until"] is None
    assert "model" in data
    assert data["scheduler"] in {"running", "stopped", "disabled"}


async def test_health_db_failure() -> None:
    """GET /api/health returns db='error' gracefully when the DB ping fails."""
    app = create_app()

    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("Simulated DB failure")

    async def override_broken_db() -> AsyncGenerator[BrokenSession, None]:
        yield BrokenSession()

    app.dependency_overrides[get_db] = override_broken_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "error"
    assert data["last_scored_at"] is None
    assert data["weather_data_until"] is None


def test_settings_defaults() -> None:
    """Settings loads expected defaults when no env overrides are provided."""
    settings = Settings(_env_file=None)
    assert settings.database_url == "mysql+asyncmy://wria:wria@localhost:3306/wria"
    assert settings.scoring_version == "wdri_v1"
    assert settings.max_agent_steps == 6
    assert settings.chat_rate_limit_per_min == 10
    assert settings.scheduler_enabled is True
    assert settings.cors_origins_list == ["http://localhost:5173"]
    assert settings.fallback_models_list == []
