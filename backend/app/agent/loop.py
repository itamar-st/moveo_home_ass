"""Hand-written JSON-step agent loop with validation, repair, and deterministic fallback."""

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.grounding import check_final_answer, requires_tool_call
from app.agent.llm_client import LLMClientProtocol, LLMUnavailableError
from app.agent.memory import (
    create_session,
    get_session,
    load_history_for_llm,
    persist_assistant_message,
    persist_tool_message,
    persist_user_message,
)
from app.agent.prompts import build_system_prompt
from app.agent.tools import (
    TOOL_REGISTRY,
    build_artifacts,
    compact_memory_tool_line,
    compact_tool_observation,
)
from app.config import get_settings
from app.db.models import Hub
from app.schemas.agent import AgentStep, FinalAnswer, agent_step_response_format
from app.schemas.tools import Artifact, ToolResult, ToolTraceItem

logger = logging.getLogger(__name__)

MAX_REPAIRS_PER_STEP = 2
CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


class SessionNotFoundError(Exception):
    """Raised when a caller specifies a session_id that does not exist."""


@dataclass(frozen=True)
class AgentEvent:
    """Single event emitted by `run_agent` (consumed by both /chat and /chat/stream)."""

    event: str
    data: dict[str, Any]


def extract_json_text(raw: str) -> str:
    """Strip markdown code fences or surrounding whitespace from model output."""
    stripped = raw.strip()
    fence_match = CODE_FENCE_RE.match(stripped)
    if fence_match:
        return fence_match.group(1).strip()
    if not stripped.startswith("{"):
        first = stripped.find("{")
        last = stripped.rfind("}")
        if first != -1 and last > first:
            return stripped[first : last + 1]
    return stripped


def _render_result_markdown(result: ToolResult) -> str:
    data = result.data or {}
    if result.tool_name == "rank_hubs":
        items = data.get("items") or []
        lines = [f"**{result.summary}**", ""]
        for item in items:
            lines.append(
                f"- **#{item['rank']} {item['city']} ({item['code']})**: "
                f"{item['score']} ({item['tier']}) — {item.get('top_driver', '')}"
            )
        return "\n".join(lines)

    if result.tool_name == "compare_hubs":
        hubs = data.get("hubs") or []
        lines = [f"**{result.summary}**", ""]
        for h in hubs:
            scores = ", ".join(f"{k}: {v}" for k, v in (h.get("hazard_scores") or {}).items())
            lines.append(f"- **{h['city']} ({h['code']})**: {scores}")
        return "\n".join(lines)

    if result.tool_name == "explain_hub_risk":
        drivers = (data.get("details") or {}).get("drivers") or []
        lines = [
            f"**{result.summary}**",
            "",
            "Key drivers:",
            *[f"- {d}" for d in drivers[:5]],
        ]
        return "\n".join(lines)

    if result.tool_name == "get_weather_stats":
        return result.summary

    if result.tool_name == "get_active_alerts":
        alerts = data.get("alerts") or []
        if not alerts:
            return result.summary
        lines = [f"**{result.summary}**", ""]
        for a in alerts[:10]:
            lines.append(f"- **{a['hub']}**: {a['event']} ({a['severity']})")
        return "\n".join(lines)

    if result.tool_name == "list_hubs":
        hubs = data.get("hubs") or []
        lines = [f"**{result.summary}**", ""]
        for h in hubs:
            score_str = f"{h['overall_score']} ({h['overall_tier']})" if h.get("overall_score") is not None else "unscored"
            lines.append(f"- **{h['code']}** ({h['city']}, {h['state']}): {score_str}")
        return "\n".join(lines)

    return result.summary


def build_deterministic_fallback(
    user_message: str,
    tool_results: Sequence[ToolResult],
    *,
    reason: str,
) -> FinalAnswer:
    """Build a templated low-confidence FinalAnswer when LLM fails or budget runs out."""
    ok_results = [r for r in tool_results if r.ok]
    if ok_results:
        last = ok_results[-1]
        intent_map = {
            "rank_hubs": "ranking",
            "compare_hubs": "comparison",
            "get_weather_stats": "statistic",
            "explain_hub_risk": "explanation",
            "get_active_alerts": "alerts",
            "list_hubs": "general",
        }
        hubs: list[str] = []
        data = last.data or {}
        if "code" in data and isinstance(data["code"], str):
            hubs.append(data["code"])
        for item in data.get("items") or []:
            if isinstance(item, dict) and item.get("code"):
                hubs.append(str(item["code"]))
        for h in data.get("hubs") or []:
            if isinstance(h, dict) and h.get("code"):
                hubs.append(str(h["code"]))
        for a in data.get("alerts") or []:
            if isinstance(a, dict) and a.get("hub"):
                hubs.append(str(a["hub"]))

        deduped_hubs = list(dict.fromkeys(hubs))
        return FinalAnswer(
            answer_markdown=_render_result_markdown(last),
            intent=intent_map.get(last.tool_name, "general"),  # type: ignore[arg-type]
            hubs=deduped_hubs,
            key_points=[last.summary],
            reasoning=[f"Rendered directly from deterministic `{last.tool_name}` tool output."],
            assumptions=last.caveats[:4],
            confidence="low",
            confidence_rationale=f"Deterministic fallback used ({reason}).",
            follow_ups=[],
        )

    return FinalAnswer(
        answer_markdown=(
            "I could not complete this request because the language model could not produce "
            "a verified response right now. Please try again shortly."
        ),
        intent="general",
        hubs=[],
        key_points=["Language model response was unavailable or invalid."],
        reasoning=[f"Fallback triggered: {reason}."],
        assumptions=[],
        confidence="low",
        confidence_rationale=f"Deterministic fallback used without tool results ({reason}).",
        follow_ups=[],
    )


async def run_agent(
    db: AsyncSession,
    llm_client: LLMClientProtocol,
    *,
    session_id: str | None,
    user_message: str,
    max_steps: int | None = None,
    today: date | None = None,
) -> AsyncIterator[AgentEvent]:
    """Execute the multi-step JSON agent loop and yield streaming AgentEvents."""
    started_at = time.perf_counter()
    settings = get_settings()
    step_budget = max_steps or settings.max_agent_steps

    if session_id is None:
        session = await create_session(db, title=user_message.strip()[:200])
    else:
        session = await get_session(db, session_id)
        if session is None:
            raise SessionNotFoundError(f"Chat session '{session_id}' not found.")

    history_messages, prior_tool_lines = await load_history_for_llm(
        db,
        session.id,
        last_n_turns=8,
    )
    user_msg = await persist_user_message(db, session, user_message)

    yield AgentEvent(
        event="session",
        data={"session_id": session.id, "user_message_id": user_msg.id},
    )

    hubs = list((await db.execute(select(Hub).order_by(Hub.code))).scalars().all())
    known_hub_codes = {h.code for h in hubs}
    system_prompt = build_system_prompt(hubs, today=today)
    response_format = agent_step_response_format(strict_json_schema=True)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        *history_messages,
        {"role": "user", "content": user_message},
    ]

    tool_results: list[ToolResult] = []
    tool_trace: list[ToolTraceItem] = []
    last_model = settings.openrouter_model
    fallback_reason: str | None = None
    final_emitted = False

    try:
        for step_num in range(1, step_budget + 1):
            yield AgentEvent(
                event="status",
                data={"stage": "thinking", "step": step_num},
            )

            parsed_step: AgentStep | None = None
            raw_content = ""
            repair_messages = list(messages)

            for attempt in range(MAX_REPAIRS_PER_STEP + 1):
                try:
                    llm_resp = await llm_client.complete(
                        repair_messages,
                        response_format=response_format,
                    )
                    last_model = llm_resp.model
                    raw_content = llm_resp.content
                except LLMUnavailableError as exc:
                    fallback_reason = str(exc)
                    yield AgentEvent(
                        event="error",
                        data={
                            "code": "llm_unavailable",
                            "message": str(exc),
                            "retryable": True,
                        },
                    )
                    break

                try:
                    cleaned = extract_json_text(raw_content)
                    parsed_step = AgentStep.model_validate_json(cleaned)
                    break
                except (ValidationError, ValueError) as exc:
                    logger.warning(
                        "agent_step_validation_failed step=%d attempt=%d error=%s",
                        step_num,
                        attempt + 1,
                        exc,
                    )
                    if attempt < MAX_REPAIRS_PER_STEP:
                        yield AgentEvent(
                            event="status",
                            data={
                                "stage": "repairing_output",
                                "step": step_num,
                                "detail": f"Repair attempt {attempt + 1}",
                            },
                        )
                        repair_messages.append({"role": "assistant", "content": raw_content})
                        repair_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"Your previous response was invalid AgentStep JSON: {exc}. "
                                    "Respond ONLY with a single valid JSON object matching the AgentStep schema."
                                ),
                            }
                        )
                    else:
                        fallback_reason = f"Schema validation failed after {MAX_REPAIRS_PER_STEP} repairs"
                        yield AgentEvent(
                            event="error",
                            data={
                                "code": "validation_failed",
                                "message": fallback_reason,
                                "retryable": True,
                            },
                        )

            if parsed_step is None:
                break

            if parsed_step.action == "call_tool":
                assert parsed_step.tool_call is not None
                tool_name = parsed_step.tool_call.name
                raw_args = parsed_step.tool_call.arguments
                call_id = f"c{step_num}"
                spec = TOOL_REGISTRY[tool_name]

                yield AgentEvent(
                    event="status",
                    data={"stage": "calling_tool", "step": step_num, "detail": tool_name},
                )
                yield AgentEvent(
                    event="tool_call",
                    data={
                        "step": step_num,
                        "call_id": call_id,
                        "name": tool_name,
                        "arguments": raw_args,
                        "rationale": parsed_step.rationale,
                    },
                )

                t0 = time.perf_counter()
                try:
                    validated_args = spec.args_model.model_validate(raw_args)
                    result = await spec.handler(db, validated_args, today=today)
                    norm_args = validated_args.model_dump(mode="json")
                except ValidationError as exc:
                    norm_args = raw_args
                    result = ToolResult(
                        ok=False,
                        tool_name=tool_name,
                        summary=f"Invalid arguments for tool '{tool_name}': {exc}",
                        data={"validation_error": str(exc)},
                        caveats=["Fix the tool arguments and retry."],
                        sources=[],
                    )
                duration_ms = int((time.perf_counter() - t0) * 1000)

                tool_results.append(result)
                trace_item = ToolTraceItem(
                    step=step_num,
                    call_id=call_id,
                    name=tool_name,
                    arguments=norm_args,
                    rationale=parsed_step.rationale,
                    ok=result.ok,
                    summary=result.summary,
                    data=result.data,
                    caveats=result.caveats,
                    duration_ms=duration_ms,
                )
                tool_trace.append(trace_item)

                compact_line = compact_memory_tool_line(tool_name, norm_args, result)
                await persist_tool_message(
                    db,
                    session.id,
                    compact_line,
                    trace_item.model_dump(mode="json"),
                )

                yield AgentEvent(
                    event="tool_result",
                    data={
                        "step": step_num,
                        "call_id": call_id,
                        "name": tool_name,
                        "ok": result.ok,
                        "summary": result.summary,
                        "data": result.data,
                        "caveats": result.caveats,
                        "duration_ms": duration_ms,
                    },
                )

                messages.append(
                    {"role": "assistant", "content": json.dumps(parsed_step.model_dump(mode="json"))}
                )
                messages.append(
                    {"role": "user", "content": f"OBSERVATION: {compact_tool_observation(result)}"}
                )
                continue

            # action == "final_answer"
            assert parsed_step.final_answer is not None
            if requires_tool_call(
                parsed_step.final_answer,
                tool_results,
                has_prior_tool_context=bool(prior_tool_lines),
            ):
                if step_num < step_budget:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": json.dumps(parsed_step.model_dump(mode="json")),
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "OBSERVATION: Quantitative intents (ranking, comparison, statistic, "
                                "explanation) require calling a deterministic tool first. Call the "
                                "appropriate tool now before returning final_answer."
                            ),
                        }
                    )
                    continue
                fallback_reason = "Quantitative final answer lacked tool grounding"
                break

            grounding_res = check_final_answer(
                parsed_step.final_answer,
                tool_results,
                known_hub_codes,
                prior_tool_lines=prior_tool_lines,
            )
            artifacts: list[Artifact] = build_artifacts(tool_results)
            latency_ms = int((time.perf_counter() - started_at) * 1000)

            assistant_payload = {
                "final_answer": grounding_res.final_answer.model_dump(mode="json"),
                "artifacts": [a.model_dump(mode="json") for a in artifacts],
                "tool_trace": [t.model_dump(mode="json") for t in tool_trace],
                "model": last_model,
                "latency_ms": latency_ms,
                "warnings": grounding_res.warnings,
            }
            assistant_msg = await persist_assistant_message(
                db,
                session.id,
                grounding_res.final_answer.answer_markdown,
                assistant_payload,
            )

            yield AgentEvent(
                event="final",
                data={
                    "message_id": assistant_msg.id,
                    "answer": grounding_res.final_answer.model_dump(mode="json"),
                    "artifacts": [a.model_dump(mode="json") for a in artifacts],
                    "model": last_model,
                    "latency_ms": latency_ms,
                    "warnings": grounding_res.warnings,
                },
            )
            final_emitted = True
            break

        if not final_emitted:
            reason = fallback_reason or f"Step budget ({step_budget}) exhausted"
            yield AgentEvent(
                event="status",
                data={"stage": "fallback", "step": step_budget, "detail": reason},
            )
            fallback_ans = build_deterministic_fallback(
                user_message,
                tool_results,
                reason=reason,
            )
            grounding_res = check_final_answer(
                fallback_ans,
                tool_results,
                known_hub_codes,
                prior_tool_lines=prior_tool_lines,
            )
            artifacts = build_artifacts(tool_results)
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            fallback_model = "deterministic-fallback" if fallback_reason else last_model

            assistant_payload = {
                "final_answer": grounding_res.final_answer.model_dump(mode="json"),
                "artifacts": [a.model_dump(mode="json") for a in artifacts],
                "tool_trace": [t.model_dump(mode="json") for t in tool_trace],
                "model": fallback_model,
                "latency_ms": latency_ms,
                "warnings": [*grounding_res.warnings, f"Fallback active: {reason}"],
            }
            assistant_msg = await persist_assistant_message(
                db,
                session.id,
                grounding_res.final_answer.answer_markdown,
                assistant_payload,
            )

            yield AgentEvent(
                event="final",
                data={
                    "message_id": assistant_msg.id,
                    "answer": grounding_res.final_answer.model_dump(mode="json"),
                    "artifacts": [a.model_dump(mode="json") for a in artifacts],
                    "model": fallback_model,
                    "latency_ms": latency_ms,
                    "warnings": assistant_payload["warnings"],
                },
            )
    finally:
        yield AgentEvent(event="done", data={})
