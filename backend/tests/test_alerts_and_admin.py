"""Tests for simulated alerts, broadcaster, webhook notifier, scheduler, and admin/alerts APIs."""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.alerts.broadcaster import AlertBroadcaster, alert_broadcaster
from app.alerts.notifier import build_webhook_payload, send_alert_webhook
from app.alerts.simulated import (
    add_simulated_alert,
    clear_simulated_alerts,
    get_active_simulated_alerts,
)
from app.api.routes_admin import get_admin_session_maker
from app.api.routes_alerts import stream_alerts
from app.config import Settings
from app.db.models import Base, Hub, RiskAlert, RiskSnapshot, WeatherDaily
from app.db.session import get_db
from app.main import create_app
from app.scheduler import start_scheduler, stop_scheduler
from app.scoring.runner import rescore_all_hubs


def test_simulated_alerts_add_expire_and_clear() -> None:
    clear_simulated_alerts()
    t0 = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    entry = add_simulated_alert(
        "mia",
        "Extreme",
        "Hurricane Warning",
        duration_hours=2.0,
        now=t0,
    )
    assert entry["simulated"] is True
    assert entry["severity"] == "Extreme"

    active_at_1h = get_active_simulated_alerts(now=t0 + timedelta(hours=1))
    assert "MIA" in active_at_1h
    assert len(active_at_1h["MIA"]) == 1

    active_at_3h = get_active_simulated_alerts(now=t0 + timedelta(hours=3))
    assert active_at_3h == {}

    add_simulated_alert("DEN", "Severe", "Winter Storm Warning", duration_hours=4.0, now=t0)
    assert clear_simulated_alerts("DEN") == 1
    assert get_active_simulated_alerts(now=t0) == {}


def test_broadcaster_fanout_and_overflow() -> None:
    bus = AlertBroadcaster(max_queue_size=2)
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    assert bus.subscriber_count == 2

    assert bus.publish("risk_alert", {"id": 1}) == 2
    assert bus.publish("risk_alert", {"id": 2}) == 2
    assert bus.publish("risk_alert", {"id": 3}) == 2

    # Oldest item (id=1) was evicted so queue stays bounded without blocking
    assert q1.get_nowait()["data"]["id"] == 2
    assert q1.get_nowait()["data"]["id"] == 3
    assert q2.qsize() == 2

    bus.unsubscribe(q1)
    bus.unsubscribe(q2)
    assert bus.subscriber_count == 0


@respx.mock
async def test_send_alert_webhook_retries_on_5xx_and_formats_slack_payload() -> None:
    url = "https://hooks.example.test/services/abc"
    route = respx.post(url).mock(
        side_effect=[
            httpx.Response(500, text="temporary error"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    alert = {
        "id": 17,
        "hub": "MIA",
        "alert_type": "tier_change",
        "old_score": 58.1,
        "new_score": 88.1,
        "message": "MIA operational risk High → Severe (Hurricane Warning, Extreme)",
        "created_at": "2026-11-15T18:00:00Z",
    }
    hub = {"code": "MIA", "city": "Miami", "operational_score": 88.1}
    expected = build_webhook_payload(alert, hub)
    assert expected["type"] == "risk_alert"
    assert "MIA operational risk" in expected["text"]

    ok = await send_alert_webhook(url, alert, hub)
    assert ok is True
    assert route.call_count == 2
    sent_body = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert sent_body["type"] == "risk_alert"
    assert sent_body["alert"]["hub"] == "MIA"
    assert sent_body["hub"]["code"] == "MIA"


async def test_scheduler_start_stop_and_disabled() -> None:
    app = FastAPI()
    disabled_cfg = Settings(_env_file=None, scheduler_enabled=False)
    assert start_scheduler(app, disabled_cfg) is None
    assert app.state.scheduler_running is False

    enabled_cfg = Settings(_env_file=None, scheduler_enabled=True, nws_refresh_minutes=15)
    sched = start_scheduler(app, enabled_cfg)
    assert sched is not None
    assert app.state.scheduler_running is True
    job_ids = {job.id for job in sched.get_jobs()}
    assert job_ids == {"nws_rescore", "daily_weather_rescore"}

    stop_scheduler(app)
    assert app.state.scheduler_running is False


@pytest.fixture
async def m7_env(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[dict, None]:
    """Isolated SQLite DB + FastAPI app with seeded MIA hub, weather, and initial snapshot."""
    clear_simulated_alerts()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        hub = Hub(
            code="MIA",
            city="Miami",
            state="FL",
            region="Southeast",
            latitude=Decimal("25.76170"),
            longitude=Decimal("-80.19180"),
            county_fips="12086",
            timezone="America/New_York",
            aliases=["miami"],
            nri={
                "hurricane": 98.0,
                "riverine_flood": 70.0,
                "coastal_flood": 95.0,
                "winter_weather": 2.0,
                "ice_storm": 1.0,
                "cold_wave": 3.0,
                "tornado": 45.0,
                "hail": 25.0,
                "strong_wind": 50.0,
                "heat_wave": 82.0,
            },
        )
        session.add(hub)
        await session.flush()

        # Seed a few weather_daily rows and an initial baseline snapshot
        from datetime import date

        for day_offset in range(10):
            session.add(
                WeatherDaily(
                    hub_id=hub.id,
                    date=date(2025, 8, 1) + timedelta(days=day_offset),
                    temp_max_c=Decimal("33.0"),
                    temp_min_c=Decimal("26.0"),
                    precip_mm=Decimal("12.0"),
                    snowfall_cm=Decimal("0.0"),
                    gust_max_kmh=Decimal("40.0"),
                    weather_code=3,
                )
            )
        await session.commit()
        await rescore_all_hubs(
            session,
            fetch_nws=False,
            computed_at=datetime(2026, 11, 15, 12, 0, 0),
        )

    monkeypatch.setenv("ADMIN_TOKEN", "secret-admin-token")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://webhook.site/test-m7")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_admin_session_maker] = lambda: session_maker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"app": app, "client": client, "session_maker": session_maker}

    clear_simulated_alerts()
    get_settings.cache_clear()
    await engine.dispose()


async def test_admin_auth_and_unknown_hub(m7_env: dict) -> None:
    client: AsyncClient = m7_env["client"]

    # Missing token -> 401
    resp = await client.post("/api/admin/rescore", json={"refresh_nws": False})
    assert resp.status_code == 401

    # Wrong token -> 401
    resp = await client.post(
        "/api/admin/simulate-alert",
        headers={"X-Admin-Token": "wrong"},
        json={"hub": "MIA", "severity": "Extreme"},
    )
    assert resp.status_code == 401

    # Unknown hub -> 404
    resp = await client.post(
        "/api/admin/simulate-alert",
        headers={"X-Admin-Token": "secret-admin-token"},
        json={"hub": "ZZZ", "severity": "Extreme"},
    )
    assert resp.status_code == 404


@respx.mock
async def test_simulate_alert_creates_db_alert_broadcasts_and_posts_webhook(
    m7_env: dict,
) -> None:
    client: AsyncClient = m7_env["client"]
    app: FastAPI = m7_env["app"]
    session_maker: async_sessionmaker[AsyncSession] = m7_env["session_maker"]

    webhook_route = respx.post("https://webhook.site/test-m7").mock(
        return_value=httpx.Response(200, json={"received": True})
    )

    queue = alert_broadcaster.subscribe()
    try:
        resp = await client.post(
            "/api/admin/simulate-alert",
            headers={"X-Admin-Token": "secret-admin-token"},
            json={
                "hub": "MIA",
                "severity": "Extreme",
                "event": "Hurricane Warning",
                "duration_hours": 6,
            },
        )
        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}

        # Wait for the background task to complete
        tasks = list(getattr(app.state, "background_tasks", ()))
        if tasks:
            await asyncio.gather(*tasks)

        # 1. Verify RiskAlert row persisted & returned by GET /api/alerts
        alerts_resp = await client.get("/api/alerts?hub=MIA")
        assert alerts_resp.status_code == 200
        alerts = alerts_resp.json()
        assert len(alerts) == 1
        assert alerts[0]["hub"] == "MIA"
        assert alerts[0]["alert_type"] in {"tier_change", "score_delta", "new_severe_weather"}
        assert alerts[0]["new_score"] > alerts[0]["old_score"]

        # 2. Verify SSE broadcaster received scoring_run and risk_alert
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        event_names = [e["event"] for e in events]
        assert "scoring_run" in event_names
        assert "risk_alert" in event_names

        # 3. Verify webhook.site received the payload
        assert webhook_route.call_count == 1
        sent = json.loads(webhook_route.calls[0].request.content.decode("utf-8"))
        assert sent["type"] == "risk_alert"
        assert sent["alert"]["hub"] == "MIA"

        # 4. Verify DB has 2 snapshots for MIA (baseline + simulated)
        async with session_maker() as s:
            snaps = (await s.execute(select(RiskSnapshot))).scalars().all()
            assert len(snaps) == 2
            db_alerts = (await s.execute(select(RiskAlert))).scalars().all()
            assert len(db_alerts) == 1
    finally:
        alert_broadcaster.unsubscribe(queue)


async def test_alerts_stream_emits_hello_and_risk_alert(m7_env: dict) -> None:
    app: FastAPI = m7_env["app"]
    session_maker: async_sessionmaker[AsyncSession] = m7_env["session_maker"]

    disconnected = False

    class FakeRequest:
        def __init__(self) -> None:
            self.app = app

        async def is_disconnected(self) -> bool:
            return disconnected

    async def publish_soon() -> None:
        nonlocal disconnected
        await asyncio.sleep(0.05)
        alert_broadcaster.publish(
            "risk_alert",
            {
                "id": 99,
                "hub": "MIA",
                "alert_type": "tier_change",
                "old_score": 52.0,
                "new_score": 82.0,
                "message": "MIA operational risk High → Severe",
                "created_at": "2026-11-15T18:00:00Z",
            },
        )
        await asyncio.sleep(0.05)
        disconnected = True

    publisher = asyncio.create_task(publish_soon())
    received: list[tuple[str, dict]] = []

    async with session_maker() as db:
        sse_resp = await stream_alerts(FakeRequest(), db)
        assert sse_resp.headers.get("X-Accel-Buffering") == "no"
        async for sse_event in sse_resp.body_iterator:
            received.append((sse_event.event, json.loads(sse_event.data)))

    await publisher
    event_names = [name for name, _ in received]
    assert event_names[0] == "hello"
    assert "risk_alert" in event_names
    risk_payload = next(data for name, data in received if name == "risk_alert")
    assert risk_payload["hub"] == "MIA"
    assert risk_payload["id"] == 99
