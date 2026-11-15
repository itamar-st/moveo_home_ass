"""Tests for 3-model LLM selection across API, DB persistence, and OpenRouter client."""

import json
from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from openai import NotFoundError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.llm_client import LLMResponse, OpenRouterLLMClient
from app.api.deps import get_db
from app.api.routes_chat import get_llm_client
from app.config import Settings
from app.db.models import Base, ChatSession
from app.main import create_app
from app.scoring.runner import rescore_all_hubs
from scripts.seed import seed_hubs


class ModelTrackingLLM:
    """FakeLLM that records the requested model on each completion call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requested_models: list[str | None] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        _ = (messages, response_format)
        self.requested_models.append(model)
        if not self._responses:
            raise AssertionError("ModelTrackingLLM exhausted")
        return LLMResponse(
            content=self._responses.pop(0),
            model=model or "nvidia/nemotron-3.5-super-vl:free",
        )


@pytest.fixture
async def model_db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await seed_hubs(session)
        await rescore_all_hubs(
            session,
            fetch_nws=False,
            computed_at=datetime(2026, 11, 15, 18, 0, 0),
            reference_date=date(2026, 11, 15),
        )
        yield session

    await engine.dispose()


async def test_meta_models_returns_three_options(model_db: AsyncSession) -> None:
    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield model_db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/meta/models")

    assert resp.status_code == 200
    body = resp.json()
    assert body["default_model"] == "nvidia/nemotron-3.5-super-vl:free"
    ids = [m["id"] for m in body["models"]]
    assert ids == [
        "nvidia/nemotron-3.5-super-vl:free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
    ]


async def test_chat_persists_and_switches_session_model_in_db(model_db: AsyncSession) -> None:
    fake_llm = ModelTrackingLLM(
        [
            json.dumps(
                {
                    "rationale": "List hubs.",
                    "action": "call_tool",
                    "tool_call": {"name": "list_hubs", "arguments": {}},
                }
            ),
            json.dumps(
                {
                    "rationale": "Answer with hub count.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "There are 20 US distribution hubs in the portfolio.",
                        "intent": "general",
                        "hubs": [],
                        "key_points": ["20 hubs covered."],
                        "reasoning": ["Queried list_hubs."],
                        "assumptions": ["Portfolio hubs only."],
                        "confidence": "high",
                        "confidence_rationale": "Verified via list_hubs.",
                        "follow_ups": ["Rank all hubs"],
                    },
                }
            ),
            json.dumps(
                {
                    "rationale": "List hubs again with lightning.",
                    "action": "call_tool",
                    "tool_call": {"name": "list_hubs", "arguments": {}},
                }
            ),
            json.dumps(
                {
                    "rationale": "Answer with hub count.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Confirmed: 20 hubs in the portfolio.",
                        "intent": "general",
                        "hubs": [],
                        "key_points": ["20 hubs covered."],
                        "reasoning": ["Queried list_hubs."],
                        "assumptions": ["Portfolio hubs only."],
                        "confidence": "high",
                        "confidence_rationale": "Verified via list_hubs.",
                        "follow_ups": ["Compare Miami and Houston"],
                    },
                }
            ),
        ]
    )

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield model_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        turn1 = await client.post(
            "/api/chat",
            json={
                "message": "Which hubs are in the portfolio?",
                "model": "google/gemma-4-31b-it:free",
            },
        )
        assert turn1.status_code == 200
        t1_body = turn1.json()
        assert t1_body["model"] == "google/gemma-4-31b-it:free"
        session_id = t1_body["session_id"]

        db_session_row = await model_db.scalar(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        assert db_session_row is not None
        assert db_session_row.model == "google/gemma-4-31b-it:free"

        turn2 = await client.post(
            "/api/chat",
            json={
                "session_id": session_id,
                "message": "Confirm the hub count.",
                "model": "nvidia/nemotron-3.5-lightning:free",
            },
        )
        assert turn2.status_code == 200
        assert turn2.json()["model"] == "nvidia/nemotron-3.5-lightning:free"

        await model_db.refresh(db_session_row)
        assert db_session_row.model == "nvidia/nemotron-3.5-lightning:free"

        invalid = await client.post(
            "/api/chat",
            json={"message": "Hello", "model": "unknown/nonexistent:free"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_model"

    assert fake_llm.requested_models == [
        "google/gemma-4-31b-it:free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
        "nvidia/nemotron-3.5-lightning:free",
    ]


async def test_openrouter_client_falls_back_to_plain_json_when_response_format_unsupported() -> None:
    settings = Settings(openrouter_api_key="sk-test-key")
    client = OpenRouterLLMClient(settings=settings)

    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    not_found = NotFoundError(
        "No endpoints found that can handle the requested parameters",
        response=httpx.Response(404, request=req),
        body=None,
    )

    ok_choice = MagicMock()
    ok_choice.message.content = '{"rationale": "ok", "action": "final_answer"}'
    ok_response = MagicMock()
    ok_response.choices = [ok_choice]
    ok_response.model = "nvidia/nemotron-3.5-lightning:free"
    ok_response.usage = None

    mock_create = AsyncMock(side_effect=[not_found, not_found, ok_response])
    assert client._client is not None
    client._client.chat.completions.create = mock_create  # type: ignore[method-assign]

    resp = await client.complete(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_schema", "json_schema": {"name": "AgentStep", "schema": {}}},
        model="nvidia/nemotron-3.5-lightning:free",
    )

    assert resp.model == "nvidia/nemotron-3.5-lightning:free"
    assert mock_create.call_count == 3
    assert mock_create.call_args_list[0].kwargs["model"] == "nvidia/nemotron-3.5-lightning:free"
    assert "response_format" not in mock_create.call_args_list[2].kwargs
