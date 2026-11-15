"""Tests for M5 SSE chat streaming (`POST /api/chat/stream`) and per-IP rate limiting."""

import json
from collections.abc import AsyncGenerator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.llm_client import LLMResponse, LLMUnavailableError
from app.api.deps import SlidingWindowRateLimiter, get_db
from app.api.routes_chat import get_llm_client
from app.db.models import Base, Hub, WeatherDaily
from app.main import create_app
from app.scoring.runner import rescore_all_hubs
from scripts.seed import seed_hubs


class ScriptedStreamLLM:
    """FakeLLM serving queued scripted completions for SSE tests."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if not self._responses:
            raise AssertionError("ScriptedStreamLLM exhausted")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return LLMResponse(content=nxt, model="nvidia/nemotron-3.5-super-vl:free")


def parse_sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse raw `text/event-stream` text into `(event_name, json_data)` pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    normalized = body.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
    return events


@pytest.fixture
async def stream_db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await seed_hubs(session)
        hubs = {h.code: h for h in (await session.execute(select(Hub))).scalars().all()}

        rows: list[WeatherDaily] = []
        start_2025 = date(2025, 1, 1)
        for day_idx in range(365):
            cur = start_2025 + timedelta(days=day_idx)
            rows.append(
                WeatherDaily(
                    hub_id=hubs["MSP"].id,
                    date=cur,
                    temp_max_c=Decimal("10.0"),
                    temp_min_c=Decimal("-15.0") if day_idx < 25 else Decimal("0.0"),
                    precip_mm=Decimal("1.0"),
                    snowfall_cm=Decimal("3.5") if day_idx < 15 else Decimal("0.0"),
                    gust_max_kmh=Decimal("30.0"),
                    weather_code=73 if day_idx < 15 else 1,
                )
            )
            rows.append(
                WeatherDaily(
                    hub_id=hubs["ORD"].id,
                    date=cur,
                    temp_max_c=Decimal("12.0"),
                    temp_min_c=Decimal("-12.0") if day_idx < 12 else Decimal("2.0"),
                    precip_mm=Decimal("2.0"),
                    snowfall_cm=Decimal("3.0") if day_idx < 10 else Decimal("0.0"),
                    gust_max_kmh=Decimal("32.0"),
                    weather_code=71 if day_idx < 10 else 1,
                )
            )
        session.add_all(rows)
        await session.commit()

        await rescore_all_hubs(
            session,
            fetch_nws=False,
            computed_at=datetime(2026, 11, 15, 18, 0, 0),
            reference_date=date(2026, 11, 15),
        )
        yield session

    await engine.dispose()


async def test_chat_stream_emits_expected_sse_sequence(stream_db: AsyncSession) -> None:
    fake_llm = ScriptedStreamLLM(
        [
            json.dumps(
                {
                    "rationale": "Rank Midwest hubs by winter exposure.",
                    "action": "call_tool",
                    "tool_call": {
                        "name": "rank_hubs",
                        "arguments": {"hazard": "winter", "region": "Midwest", "top_n": 3},
                    },
                }
            ),
            json.dumps(
                {
                    "rationale": "Summarize winter ranking.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Minneapolis (MSP) leads Midwest hubs for winter risk.",
                        "intent": "ranking",
                        "hubs": ["MSP", "ORD"],
                        "key_points": ["MSP ranks #1 in Midwest winter exposure."],
                        "reasoning": ["Used rank_hubs with hazard=winter, region=Midwest."],
                        "assumptions": ["Structural WDRI v1 weights applied."],
                        "confidence": "high",
                        "confidence_rationale": "All Midwest hubs scored.",
                        "follow_ups": ["What about Chicago?"],
                    },
                }
            ),
        ]
    )

    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield stream_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/chat/stream",
            json={"message": "Which hubs in the Midwest are most exposed to winter disruption?"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        assert resp.headers.get("x-accel-buffering") == "no"

        events = parse_sse_events(resp.text)
        event_names = [name for name, _ in events]
        assert event_names == [
            "session",
            "status",
            "status",
            "tool_call",
            "tool_result",
            "status",
            "final",
            "done",
        ]

        session_ev = events[0][1]
        session_id = session_ev["session_id"]
        assert session_ev["user_message_id"] > 0

        tool_call_ev = events[3][1]
        assert tool_call_ev["name"] == "rank_hubs"
        assert tool_call_ev["step"] == 1

        tool_res_ev = events[4][1]
        assert tool_res_ev["ok"] is True
        assert tool_res_ev["data"]["items"][0]["code"] == "MSP"

        final_ev = events[6][1]
        assert final_ev["answer"]["intent"] == "ranking"
        assert final_ev["artifacts"][0]["type"] == "ranking_table"
        assert final_ev["model"] == "nvidia/nemotron-3.5-super-vl:free"

        # Verify DB messages persisted during stream
        msgs = await client.get(f"/api/chat/sessions/{session_id}/messages")
        assert msgs.status_code == 200
        assert [m["role"] for m in msgs.json()] == ["user", "tool", "assistant"]


async def test_chat_stream_unknown_session_returns_404(stream_db: AsyncSession) -> None:
    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield stream_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm_client] = lambda: ScriptedStreamLLM([])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/chat/stream",
            json={
                "session_id": "00000000-0000-0000-0000-000000000000",
                "message": "Hello",
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "session_not_found"


async def test_chat_stream_llm_unavailable_emits_error_and_fallback(
    stream_db: AsyncSession,
) -> None:
    fake_llm = ScriptedStreamLLM([LLMUnavailableError("OpenRouter 429 rate limited")])

    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield stream_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/chat/stream",
            json={"message": "Which hub has the highest risk?"},
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        names = [name for name, _ in events]
        assert "error" in names
        assert "final" in names
        assert names[-1] == "done"

        err_payload = dict(events)[ "error" ]
        assert err_payload["code"] == "llm_unavailable"
        final_payload = dict(events)["final"]
        assert final_payload["model"] == "deterministic-fallback"
        assert final_payload["answer"]["confidence"] == "low"


async def test_chat_rate_limiter_enforces_per_ip_limit(
    stream_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    get_settings.cache_clear()

    now = [100.0]
    fake_llm = ScriptedStreamLLM(
        [
            json.dumps(
                {
                    "rationale": "Direct reply",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Ready to help.",
                        "intent": "general",
                        "hubs": [],
                        "key_points": ["Portfolio covers 20 US hubs."],
                        "reasoning": ["General assistance query."],
                        "assumptions": [],
                        "confidence": "high",
                        "confidence_rationale": "No data lookup needed.",
                        "follow_ups": [],
                    },
                }
            )
            for _ in range(5)
        ]
    )

    app = create_app()
    app.state.chat_rate_limiter = SlidingWindowRateLimiter(
        limit=2,
        window_seconds=60.0,
        clock=lambda: now[0],
    )

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield stream_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Spoofed leftmost hop is ignored; rightmost hop (10.0.0.1) is rate-limited
            r1 = await client.post(
                "/api/chat",
                json={"message": "Hello"},
                headers={"X-Forwarded-For": "198.51.100.1, 10.0.0.1"},
            )
            assert r1.status_code == 200

            r2 = await client.post(
                "/api/chat/stream",
                json={"message": "Hello again"},
                headers={"X-Forwarded-For": "198.51.100.2, 10.0.0.1"},
            )
            assert r2.status_code == 200

            r3 = await client.post(
                "/api/chat/stream",
                json={"message": "One too many"},
                headers={"X-Forwarded-For": "198.51.100.3, 10.0.0.1"},
            )
            assert r3.status_code == 429
            assert r3.json()["error"]["code"] == "rate_limited"
            assert int(r3.headers["Retry-After"]) >= 1

            # Distinct IP is unaffected
            r_other = await client.post(
                "/api/chat",
                json={"message": "Hello from another IP"},
                headers={"X-Forwarded-For": "10.0.0.2"},
            )
            assert r_other.status_code == 200

            # Advancing clock past 60s restores quota
            now[0] += 61.0
            r_after = await client.post(
                "/api/chat",
                json={"message": "After window"},
                headers={"X-Forwarded-For": "10.0.0.1"},
            )
            assert r_after.status_code == 200
    finally:
        get_settings.cache_clear()


async def test_chat_rate_limiter_ignores_forwarded_for_when_untrusted(
    stream_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")
    get_settings.cache_clear()

    now = [100.0]
    fake_llm = ScriptedStreamLLM(
        [
            json.dumps(
                {
                    "rationale": "Direct reply",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Ready to help.",
                        "intent": "general",
                        "hubs": [],
                        "key_points": ["Portfolio covers 20 US hubs."],
                        "reasoning": ["General assistance query."],
                        "assumptions": [],
                        "confidence": "high",
                        "confidence_rationale": "No data lookup needed.",
                        "follow_ups": [],
                    },
                }
            )
            for _ in range(3)
        ]
    )

    app = create_app()
    app.state.chat_rate_limiter = SlidingWindowRateLimiter(
        limit=1,
        window_seconds=60.0,
        clock=lambda: now[0],
    )

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield stream_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.post(
                "/api/chat",
                json={"message": "First"},
                headers={"X-Forwarded-For": "10.0.0.1"},
            )
            assert r1.status_code == 200

            # Rotating X-Forwarded-For must NOT bypass rate limiting when proxy headers are untrusted
            r2 = await client.post(
                "/api/chat",
                json={"message": "Second"},
                headers={"X-Forwarded-For": "10.0.0.99"},
            )
            assert r2.status_code == 429
    finally:
        get_settings.cache_clear()


async def test_rate_limiter_bounds_bucket_memory() -> None:
    now = [0.0]
    limiter = SlidingWindowRateLimiter(
        limit=5,
        window_seconds=60.0,
        max_buckets=3,
        clock=lambda: now[0],
    )
    assert (await limiter.check("ip1"))[0] is True
    assert (await limiter.check("ip2"))[0] is True
    assert (await limiter.check("ip3"))[0] is True
    assert (await limiter.check("ip4"))[0] is True
    assert len(limiter._buckets) == 3
    assert "ip1" not in limiter._buckets
