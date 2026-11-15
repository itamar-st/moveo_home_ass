"""End-to-end REST tests for M4 chat endpoints integrated with M3 snapshots."""

import json
from collections.abc import AsyncGenerator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.llm_client import LLMResponse
from app.api.deps import get_db
from app.api.routes_chat import get_llm_client
from app.db.models import Base, Hub, WeatherDaily
from app.main import create_app
from app.scoring.runner import rescore_all_hubs
from scripts.seed import seed_hubs


class ScriptedChatLLM:
    """FakeLLM serving queued scripted completions across API calls."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if not self._responses:
            raise AssertionError("ScriptedChatLLM exhausted")
        return LLMResponse(content=self._responses.pop(0), model="nvidia/nemotron-3.5-super-vl:free")


@pytest.fixture
async def chat_db() -> AsyncGenerator[AsyncSession, None]:
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
            rows.append(
                WeatherDaily(
                    hub_id=hubs["DEN"].id,
                    date=cur,
                    temp_max_c=Decimal("18.0"),
                    temp_min_c=Decimal("-4.0"),
                    precip_mm=Decimal("1.0"),
                    snowfall_cm=Decimal("0.5") if day_idx < 73 else Decimal("0.0"),
                    gust_max_kmh=Decimal("30.0"),
                    weather_code=71 if day_idx < 73 else 1,
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


async def test_chat_sessions_post_and_messages_lifecycle(chat_db: AsyncSession) -> None:
    fake_llm = ScriptedChatLLM(
        [
            # Turn 1: Q1 Midwest winter
            json.dumps(
                {
                    "rationale": "Rank Midwest hubs for winter exposure.",
                    "action": "call_tool",
                    "tool_call": {
                        "name": "rank_hubs",
                        "arguments": {"hazard": "winter", "region": "Midwest", "top_n": 3},
                    },
                }
            ),
            json.dumps(
                {
                    "rationale": "Summarize ranking results.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Minneapolis (MSP) is the highest-risk Midwest hub for winter disruption.",
                        "intent": "ranking",
                        "hubs": ["MSP", "ORD"],
                        "key_points": ["MSP ranks #1 in Midwest winter exposure."],
                        "reasoning": ["Used deterministic winter ranking tool."],
                        "assumptions": ["Structural WDRI v1 weights applied."],
                        "confidence": "high",
                        "confidence_rationale": "All Midwest hubs scored.",
                        "follow_ups": ["What about Chicago specifically?"],
                    },
                }
            ),
            # Turn 2: follow-up "what about Chicago?"
            json.dumps(
                {
                    "rationale": "Explain Chicago (ORD) breakdown.",
                    "action": "call_tool",
                    "tool_call": {
                        "name": "explain_hub_risk",
                        "arguments": {"hub": "Chicago"},
                    },
                }
            ),
            json.dumps(
                {
                    "rationale": "Deliver Chicago explanation.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Chicago (ORD) faces winter and flood risk drivers.",
                        "intent": "explanation",
                        "hubs": ["ORD"],
                        "key_points": ["ORD scored across 5 hazard families."],
                        "reasoning": ["Inspected ORD snapshot via explain_hub_risk."],
                        "assumptions": ["ERA5 reanalysis climatology."],
                        "confidence": "high",
                        "confidence_rationale": "Complete ORD snapshot.",
                        "follow_ups": [],
                    },
                }
            ),
        ]
    )

    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield chat_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create session
        sess_resp = await client.post("/api/chat/sessions", json={})
        assert sess_resp.status_code == 201
        session_id = sess_resp.json()["session_id"]

        # 2. Ask Q1 in session
        r1 = await client.post(
            "/api/chat",
            json={
                "session_id": session_id,
                "message": "Which hubs in the Midwest are most exposed to winter disruption?",
            },
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["session_id"] == session_id
        assert d1["answer"]["intent"] == "ranking"
        assert d1["artifacts"][0]["type"] == "ranking_table"
        assert d1["tool_trace"][0]["name"] == "rank_hubs"
        assert d1["tool_trace"][0]["ok"] is True

        # 3. Follow-up in same session
        r2 = await client.post(
            "/api/chat",
            json={
                "session_id": session_id,
                "message": "what about Chicago?",
            },
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["answer"]["hubs"] == ["ORD"]
        assert d2["artifacts"][0]["type"] == "score_breakdown"

        # 4. List messages
        msgs_resp = await client.get(f"/api/chat/sessions/{session_id}/messages")
        assert msgs_resp.status_code == 200
        msgs = msgs_resp.json()
        assert [m["role"] for m in msgs] == ["user", "tool", "assistant", "user", "tool", "assistant"]

        # 5. Unknown session returns 404 in §8 error envelope
        missing_id = "00000000-0000-0000-0000-000000000000"
        not_found = await client.get(f"/api/chat/sessions/{missing_id}/messages")
        assert not_found.status_code == 404
        assert not_found.json()["error"]["code"] == "session_not_found"

        post_not_found = await client.post(
            "/api/chat",
            json={"session_id": missing_id, "message": "Hello"},
        )
        assert post_not_found.status_code == 404
        assert post_not_found.json()["error"]["code"] == "session_not_found"

        # 6. Verify M3 read endpoint still works alongside M4
        rankings_resp = await client.get("/api/rankings?hazard=winter&region=Midwest")
        assert rankings_resp.status_code == 200
        assert len(rankings_resp.json()["items"]) == 6


async def test_chat_api_covers_q2_q3_q4(chat_db: AsyncSession) -> None:
    """Verify Q2 (compare), Q3 (weather stats), and Q4 (explain) end-to-end via POST /api/chat."""
    fake_llm = ScriptedChatLLM(
        [
            # Q2: compare Miami and Houston
            json.dumps(
                {
                    "rationale": "Compare Miami and Houston for hurricane and flood exposure.",
                    "action": "call_tool",
                    "tool_call": {
                        "name": "compare_hubs",
                        "arguments": {"hubs": ["Miami", "Houston"], "hazards": ["hurricane", "flood"]},
                    },
                }
            ),
            json.dumps(
                {
                    "rationale": "Summarize comparison.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Miami (MIA) and Houston (IAH) both face elevated hurricane and flood risk.",
                        "intent": "comparison",
                        "hubs": ["MIA", "IAH"],
                        "key_points": ["Compared MIA and IAH on hurricane and flood."],
                        "reasoning": ["Used compare_hubs deterministic tool."],
                        "assumptions": ["County-level FEMA NRI percentiles used."],
                        "confidence": "high",
                        "confidence_rationale": "Both hubs scored.",
                        "follow_ups": [],
                    },
                }
            ),
            # Q3: Denver snowfall last year
            json.dumps(
                {
                    "rationale": "Get Denver snowfall days percentage for last year.",
                    "action": "call_tool",
                    "tool_call": {
                        "name": "get_weather_stats",
                        "arguments": {
                            "hub": "Denver",
                            "metric": "snowfall_days",
                            "period": "last_year",
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "rationale": "Report Denver snowfall percentage.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Denver (DEN) had snowfall on 20.0% of days (73 of 365 days) in 2025.",
                        "intent": "statistic",
                        "hubs": ["DEN"],
                        "key_points": ["73 days out of 365 (20.0%) had >= 0.1 cm snowfall."],
                        "reasoning": ["Computed via get_weather_stats over calendar 2025."],
                        "assumptions": ["Last year = calendar 2025."],
                        "confidence": "high",
                        "confidence_rationale": "100% daily coverage for Denver in 2025.",
                        "follow_ups": [],
                    },
                }
            ),
            # Q4: Explain Dallas hub risk
            json.dumps(
                {
                    "rationale": "Explain Dallas (DFW) hub risk drivers.",
                    "action": "call_tool",
                    "tool_call": {
                        "name": "explain_hub_risk",
                        "arguments": {"hub": "Dallas"},
                    },
                }
            ),
            json.dumps(
                {
                    "rationale": "Summarize Dallas drivers.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Dallas (DFW) risk is driven by severe storms, flooding, and heat.",
                        "intent": "explanation",
                        "hubs": ["DFW"],
                        "key_points": ["DFW risk breakdown retrieved from snapshot."],
                        "reasoning": ["Used explain_hub_risk."],
                        "assumptions": ["Structural WDRI v1 weights applied."],
                        "confidence": "high",
                        "confidence_rationale": "Snapshot available for DFW.",
                        "follow_ups": [],
                    },
                }
            ),
        ]
    )

    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield chat_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        q2 = await client.post(
            "/api/chat",
            json={"message": "Compare Miami and Houston in terms of hurricane and flood exposure."},
        )
        assert q2.status_code == 200
        assert q2.json()["artifacts"][0]["type"] == "comparison"
        assert set(q2.json()["answer"]["hubs"]) == {"MIA", "IAH"}

        q3 = await client.post(
            "/api/chat",
            json={"message": "What percentage of days in Denver last year had snowfall?"},
        )
        assert q3.status_code == 200
        assert q3.json()["artifacts"][0]["type"] == "stat"
        assert q3.json()["warnings"] == []

        q4 = await client.post(
            "/api/chat",
            json={"message": "Why is the Dallas hub's weather disruption risk high?"},
        )
        assert q4.status_code == 200
        assert q4.json()["artifacts"][0]["type"] == "score_breakdown"
        assert q4.json()["answer"]["hubs"] == ["DFW"]
