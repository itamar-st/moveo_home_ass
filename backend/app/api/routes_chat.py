"""Chat sessions, message history, synchronous chat, and SSE streaming endpoints (M4/M5)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from app.agent.llm_client import LLMClientProtocol, OpenRouterLLMClient
from app.agent.loop import SessionNotFoundError, run_agent
from app.agent.memory import create_session, get_session, get_session_messages
from app.api.deps import APIError, enforce_chat_rate_limit, get_db
from app.schemas.agent import FinalAnswer
from app.schemas.api import (
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    CreateSessionResponse,
)
from app.schemas.tools import Artifact, ToolTraceItem

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


def get_llm_client() -> LLMClientProtocol:
    """FastAPI dependency providing the configured OpenRouter LLM client."""
    return OpenRouterLLMClient()


@router.post("/sessions", status_code=201, response_model=CreateSessionResponse)
async def create_chat_session(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: Annotated[dict[str, Any] | None, Body()] = None,
) -> CreateSessionResponse:
    """Create a new persistent chat session."""
    _ = body
    session = await create_session(db)
    return CreateSessionResponse(session_id=session.id, created_at=session.created_at)


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def list_chat_messages(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ChatMessageOut]:
    """Return ordered message history for a chat session."""
    session = await get_session(db, session_id)
    if session is None:
        raise APIError(
            404,
            "session_not_found",
            f"Chat session '{session_id}' not found.",
            details={"session_id": session_id},
        )

    messages = await get_session_messages(db, session_id)
    return [
        ChatMessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            payload=m.payload,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.post(
    "",
    response_model=ChatResponse,
    dependencies=[Depends(enforce_chat_rate_limit)],
)
async def post_chat(
    request: ChatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    llm_client: Annotated[LLMClientProtocol, Depends(get_llm_client)],
) -> ChatResponse:
    """Execute one conversational turn synchronously and return the structured response."""
    resolved_session_id = str(request.session_id) if request.session_id else None
    actual_session_id = resolved_session_id or ""
    tool_trace: list[ToolTraceItem] = []
    final_data: dict[str, Any] | None = None

    try:
        async for event in run_agent(
            db,
            llm_client,
            session_id=resolved_session_id,
            user_message=request.message,
        ):
            if event.event == "session":
                actual_session_id = str(event.data["session_id"])
            elif event.event == "tool_call":
                tool_trace.append(
                    ToolTraceItem(
                        step=int(event.data["step"]),
                        call_id=str(event.data["call_id"]),
                        name=str(event.data["name"]),
                        arguments=dict(event.data.get("arguments") or {}),
                        rationale=str(event.data.get("rationale") or ""),
                        ok=False,
                        summary="",
                    )
                )
            elif event.event == "tool_result":
                matching_call = next(
                    (t for t in tool_trace if t.call_id == event.data["call_id"]),
                    None,
                )
                if matching_call is not None:
                    matching_call.ok = bool(event.data["ok"])
                    matching_call.summary = str(event.data["summary"])
                    matching_call.data = dict(event.data.get("data") or {})
                    matching_call.caveats = list(event.data.get("caveats") or [])
                    matching_call.duration_ms = int(event.data.get("duration_ms") or 0)
            elif event.event == "final":
                final_data = event.data
    except SessionNotFoundError as exc:
        raise APIError(
            404,
            "session_not_found",
            str(exc),
            details={"session_id": resolved_session_id},
        ) from exc

    if final_data is None:
        raise APIError(500, "internal", "Agent finished without emitting a final response.")

    return ChatResponse(
        session_id=actual_session_id,
        message_id=int(final_data["message_id"]),
        answer=FinalAnswer.model_validate(final_data["answer"]),
        artifacts=[Artifact.model_validate(a) for a in (final_data.get("artifacts") or [])],
        tool_trace=tool_trace,
        model=str(final_data["model"]),
        latency_ms=int(final_data["latency_ms"]),
        warnings=list(final_data.get("warnings") or []),
    )


@router.post(
    "/stream",
    dependencies=[Depends(enforce_chat_rate_limit)],
)
async def post_chat_stream(
    chat_request: ChatRequest,
    http_request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    llm_client: Annotated[LLMClientProtocol, Depends(get_llm_client)],
) -> EventSourceResponse:
    """Stream one conversational turn as §8.2 Server-Sent Events."""
    resolved_session_id = str(chat_request.session_id) if chat_request.session_id else None
    if resolved_session_id is not None:
        existing = await get_session(db, resolved_session_id)
        if existing is None:
            raise APIError(
                404,
                "session_not_found",
                f"Chat session '{resolved_session_id}' not found.",
                details={"session_id": resolved_session_id},
            )

    async def event_generator() -> AsyncIterator[ServerSentEvent]:
        agent_iter = run_agent(
            db,
            llm_client,
            session_id=resolved_session_id,
            user_message=chat_request.message,
        )
        try:
            async for agent_event in agent_iter:
                if await http_request.is_disconnected():
                    logger.info(
                        "chat_stream_client_disconnected session_id=%s",
                        resolved_session_id,
                    )
                    break
                yield ServerSentEvent(
                    event=agent_event.event,
                    data=json.dumps(agent_event.data, default=str),
                )
        except asyncio.CancelledError:
            logger.info("chat_stream_cancelled session_id=%s", resolved_session_id)
            raise
        except Exception as exc:
            logger.exception("chat_stream_error session_id=%s", resolved_session_id)
            yield ServerSentEvent(
                event="error",
                data=json.dumps(
                    {
                        "code": "internal",
                        "message": str(exc),
                        "retryable": False,
                    }
                ),
            )
            yield ServerSentEvent(event="done", data="{}")
        finally:
            aclose = getattr(agent_iter, "aclose", None)
            if callable(aclose):
                await aclose()

    return EventSourceResponse(
        event_generator(),
        ping=15,
        ping_message_factory=lambda: ServerSentEvent(comment="ping"),
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
