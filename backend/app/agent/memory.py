"""Conversation session and message persistence + history replay (AGENTS.md §7.7)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, ChatSession


async def create_session(
    db: AsyncSession,
    *,
    title: str | None = None,
    model: str | None = None,
) -> ChatSession:
    """Create and persist a new chat session with a UUID4 identifier."""
    session = ChatSession(
        id=str(uuid.uuid4()),
        title=title[:200] if title else None,
        model=model[:120] if model else None,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def update_session_model(
    db: AsyncSession,
    session: ChatSession,
    model: str,
) -> None:
    """Update the persisted model on an existing chat session if it changed."""
    if session.model == model:
        return
    session.model = model[:120]
    await db.commit()
    await db.refresh(session)


async def get_session(db: AsyncSession, session_id: str) -> ChatSession | None:
    """Look up a chat session by UUID."""
    return await db.scalar(select(ChatSession).where(ChatSession.id == session_id))


async def get_session_messages(db: AsyncSession, session_id: str) -> list[ChatMessage]:
    """Return all persisted messages for a session in chronological order."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def persist_user_message(
    db: AsyncSession,
    session: ChatSession,
    content: str,
) -> ChatMessage:
    """Persist a user message and populate session title on first turn."""
    if not session.title:
        session.title = content.strip()[:200]
    msg = ChatMessage(
        session_id=session.id,
        role="user",
        content=content,
        payload=None,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def persist_tool_message(
    db: AsyncSession,
    session_id: str,
    compact_summary: str,
    payload: dict[str, Any],
) -> ChatMessage:
    """Persist a tool invocation summary and structured payload."""
    msg = ChatMessage(
        session_id=session_id,
        role="tool",
        content=compact_summary,
        payload=payload,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def persist_assistant_message(
    db: AsyncSession,
    session_id: str,
    answer_markdown: str,
    payload: dict[str, Any],
) -> ChatMessage:
    """Persist the final assistant answer and structured metadata payload."""
    msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=answer_markdown,
        payload=payload,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def load_history_for_llm(
    db: AsyncSession,
    session_id: str,
    *,
    last_n_turns: int = 8,
) -> tuple[list[dict[str, str]], list[str]]:
    """Load the last N turns as LLM chat messages plus compact prior tool lines.

    Tool messages are emitted as `user` observation lines so providers without
    native `tool_calls` support accept the message sequence cleanly.
    """
    all_messages = await get_session_messages(db, session_id)
    if not all_messages:
        return [], []

    user_indices = [idx for idx, m in enumerate(all_messages) if m.role == "user"]
    if len(user_indices) > last_n_turns:
        start_idx = user_indices[-last_n_turns]
        sliced = all_messages[start_idx:]
    else:
        sliced = all_messages

    history: list[dict[str, str]] = []
    prior_tool_lines: list[str] = []
    pending_tools: list[str] = []

    def _flush_tools() -> None:
        if pending_tools:
            history.append(
                {
                    "role": "user",
                    "content": "OBSERVATION (prior turn):\n" + "\n".join(pending_tools),
                }
            )
            pending_tools.clear()

    for msg in sliced:
        if msg.role == "user":
            _flush_tools()
            history.append({"role": "user", "content": msg.content})
        elif msg.role == "tool":
            pending_tools.append(msg.content)
            prior_tool_lines.append(msg.content)
        elif msg.role == "assistant":
            _flush_tools()
            history.append({"role": "assistant", "content": msg.content})

    _flush_tools()
    return history, prior_tool_lines
