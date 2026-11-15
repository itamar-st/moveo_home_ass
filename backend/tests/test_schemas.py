"""Tests for M4 Pydantic schemas: AgentStep, FinalAnswer, ToolCall, and tool arg models."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.agent import AgentStep, FinalAnswer, ToolCall, agent_step_response_format
from app.schemas.tools import (
    CompareHubsArgs,
    GetWeatherStatsArgs,
    RankHubsArgs,
)


def _valid_final_answer(**overrides) -> dict:
    base = {
        "answer_markdown": "Minneapolis (MSP) leads Midwest winter risk at 71.2.",
        "intent": "ranking",
        "hubs": ["MSP"],
        "key_points": ["MSP is #1 in Midwest winter risk."],
        "reasoning": ["Ranked by winter sub-score from risk snapshots."],
        "assumptions": ["Structural WDRI v1 weights used."],
        "confidence": "high",
        "confidence_rationale": "All hubs have complete snapshots.",
        "follow_ups": ["Explain MSP risk"],
    }
    base.update(overrides)
    return base


def test_agent_step_valid_call_tool() -> None:
    step = AgentStep(
        rationale="Need Midwest winter rankings.",
        action="call_tool",
        tool_call=ToolCall(
            name="rank_hubs",
            arguments={"hazard": "winter", "region": "Midwest", "top_n": 5},
        ),
        final_answer=None,
    )
    assert step.action == "call_tool"
    assert step.tool_call is not None
    assert step.tool_call.name == "rank_hubs"


def test_agent_step_valid_final_answer() -> None:
    step = AgentStep(
        rationale="Present tool ranking to user.",
        action="final_answer",
        tool_call=None,
        final_answer=FinalAnswer(**_valid_final_answer()),
    )
    assert step.action == "final_answer"
    assert step.final_answer is not None
    assert step.final_answer.intent == "ranking"


def test_agent_step_mutually_exclusive_payload_enforced() -> None:
    with pytest.raises(ValidationError, match="tool_call must be provided"):
        AgentStep(rationale="missing call", action="call_tool", tool_call=None, final_answer=None)

    with pytest.raises(ValidationError, match="final_answer must be null"):
        AgentStep(
            rationale="both set",
            action="call_tool",
            tool_call=ToolCall(name="list_hubs", arguments={}),
            final_answer=FinalAnswer(**_valid_final_answer()),
        )

    with pytest.raises(ValidationError, match="final_answer must be provided"):
        AgentStep(rationale="missing answer", action="final_answer", tool_call=None, final_answer=None)

    with pytest.raises(ValidationError, match="tool_call must be null"):
        AgentStep(
            rationale="both set",
            action="final_answer",
            tool_call=ToolCall(name="list_hubs", arguments={}),
            final_answer=FinalAnswer(**_valid_final_answer()),
        )


def test_agent_step_rejects_unknown_tool_and_list_limits() -> None:
    with pytest.raises(ValidationError):
        ToolCall(name="unknown_tool", arguments={})  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        FinalAnswer(**_valid_final_answer(key_points=["a"] * 7))

    with pytest.raises(ValidationError):
        FinalAnswer(**_valid_final_answer(reasoning=["r"] * 7))

    with pytest.raises(ValidationError):
        FinalAnswer(**_valid_final_answer(follow_ups=["f"] * 4))


def test_tool_args_validation_rules() -> None:
    with pytest.raises(ValidationError):
        RankHubsArgs(top_n=0)

    with pytest.raises(ValidationError):
        RankHubsArgs(top_n=21)

    with pytest.raises(ValidationError):
        CompareHubsArgs(hubs=["MIA"])

    with pytest.raises(ValidationError):
        CompareHubsArgs(hubs=["MIA", "IAH", "DFW", "DEN", "ORD", "MSP"])

    with pytest.raises(ValidationError, match="start_date and end_date are required"):
        GetWeatherStatsArgs(hub="DEN", metric="snowfall_days", period="custom")

    with pytest.raises(ValidationError, match="start_date must be on or before end_date"):
        GetWeatherStatsArgs(
            hub="DEN",
            metric="snowfall_days",
            period="custom",
            start_date=date(2025, 5, 10),
            end_date=date(2025, 5, 1),
        )


def test_agent_step_response_format_shapes() -> None:
    strict_fmt = agent_step_response_format(strict_json_schema=True)
    assert strict_fmt["type"] == "json_schema"
    assert strict_fmt["json_schema"]["name"] == "AgentStep"
    assert "properties" in strict_fmt["json_schema"]["schema"]

    fallback_fmt = agent_step_response_format(strict_json_schema=False)
    assert fallback_fmt == {"type": "json_object"}
