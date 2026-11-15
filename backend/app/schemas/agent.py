"""Pydantic models for the structured LLM <-> agent loop protocol (AGENTS.md §7.3)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ToolName = Literal[
    "list_hubs",
    "rank_hubs",
    "compare_hubs",
    "explain_hub_risk",
    "get_weather_stats",
    "get_active_alerts",
]

AnswerIntent = Literal[
    "ranking",
    "comparison",
    "statistic",
    "explanation",
    "alerts",
    "general",
    "out_of_scope",
]

ConfidenceLevel = Literal["high", "medium", "low"]


class ToolCall(BaseModel):
    """Tool invocation requested by the LLM."""

    name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class FinalAnswer(BaseModel):
    """Structured final response emitted by the LLM."""

    answer_markdown: str = Field(min_length=1, max_length=4000)
    intent: AnswerIntent
    hubs: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list, max_length=6)
    reasoning: list[str] = Field(default_factory=list, max_length=6)
    assumptions: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel
    confidence_rationale: str = Field(min_length=1)
    follow_ups: list[str] = Field(default_factory=list, max_length=3)


class AgentStep(BaseModel):
    """Single LLM turn in the JSON-step agent loop."""

    rationale: str = Field(min_length=1, max_length=400)
    action: Literal["call_tool", "final_answer"]
    tool_call: ToolCall | None = None
    final_answer: FinalAnswer | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "AgentStep":
        """Ensure exactly one of tool_call or final_answer matches action."""
        if self.action == "call_tool":
            if self.tool_call is None:
                raise ValueError("tool_call must be provided when action='call_tool'")
            if self.final_answer is not None:
                raise ValueError("final_answer must be null when action='call_tool'")
        elif self.action == "final_answer":
            if self.final_answer is None:
                raise ValueError("final_answer must be provided when action='final_answer'")
            if self.tool_call is not None:
                raise ValueError("tool_call must be null when action='final_answer'")
        return self


def agent_step_response_format(*, strict_json_schema: bool = True) -> dict[str, Any]:
    """Build OpenRouter response_format payload for AgentStep."""
    if strict_json_schema:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "AgentStep",
                "schema": AgentStep.model_json_schema(),
            },
        }
    return {"type": "json_object"}
