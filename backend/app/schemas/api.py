"""Pydantic REST API request/response models for hubs, rankings, scoring meta, and chat."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.agent import FinalAnswer
from app.schemas.tools import Artifact, ToolTraceItem

RegionName = Literal[
    "Midwest",
    "Northeast",
    "Southeast",
    "South Central",
    "Mountain",
    "West",
]
HazardFilter = Literal[
    "overall",
    "winter",
    "hurricane",
    "flood",
    "severe_storm",
    "heat",
]
ScoreKind = Literal["structural", "operational"]


# ---------------------------------------------------------------------------
# M3: Hubs, Rankings & Scoring Metadata
# ---------------------------------------------------------------------------


class HubOut(BaseModel):
    """Hub summary with latest structural and operational scores."""

    code: str
    name: str
    city: str
    state: str
    region: RegionName
    latitude: float
    longitude: float
    county_fips: str
    overall_score: float | None = None
    overall_tier: str | None = None
    operational_score: float | None = None
    operational_tier: str | None = None


class HubDetailOut(HubOut):
    """Detailed hub response including NRI scores and latest snapshot breakdown."""

    timezone: str
    aliases: list[str] = Field(default_factory=list)
    nri: dict[str, Any] | None = None
    computed_at: datetime | None = None
    details: dict[str, Any] | None = None


class SnapshotSummaryOut(BaseModel):
    """Historical scoring snapshot summary for a hub."""

    computed_at: datetime
    overall_score: float
    overall_tier: str
    operational_score: float
    operational_tier: str
    live_bump: float = 0.0
    confidence: str | None = None


class RankingItemOut(BaseModel):
    """Single hub row in `/api/rankings`."""

    rank: int
    code: str
    name: str
    city: str
    state: str
    region: RegionName
    score: float
    tier: str
    top_driver: str | None = None
    hazard_scores: dict[str, float] = Field(default_factory=dict)
    breakdown: dict[str, Any] = Field(default_factory=dict)


class RankingsResponse(BaseModel):
    """Response for `GET /api/rankings`."""

    hazard: HazardFilter
    score_kind: ScoreKind
    region: RegionName | None = None
    scoring_version: str
    computed_at: datetime | None = None
    items: list[RankingItemOut] = Field(default_factory=list)


class ScoringMetaResponse(BaseModel):
    """Response for `GET /api/meta/scoring`."""

    scoring_version: str
    weights: dict[str, Any]
    sub_weights: dict[str, Any]
    thresholds: dict[str, Any]
    caps: dict[str, float]
    tiers: list[dict[str, Any]]
    alert_bumps: dict[str, float]
    data_sources: list[dict[str, Any]]
    assumptions: list[str]


# ---------------------------------------------------------------------------
# M4: Chat Sessions & Conversational Agent
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """Structured error body."""

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Top-level error envelope (`{"error": ...}`)."""

    error: ErrorDetail


class CreateSessionResponse(BaseModel):
    """Response for `POST /api/chat/sessions`."""

    session_id: str
    created_at: datetime


class ChatMessageOut(BaseModel):
    """Serialized message in `GET /api/chat/sessions/{id}/messages`."""

    id: int
    role: str
    content: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class ChatRequest(BaseModel):
    """Request body for `POST /api/chat` and `POST /api/chat/stream`."""

    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    """Response body for non-streaming `POST /api/chat`."""

    session_id: str
    message_id: int
    answer: FinalAnswer
    artifacts: list[Artifact] = Field(default_factory=list)
    tool_trace: list[ToolTraceItem] = Field(default_factory=list)
    model: str
    latency_ms: int
    warnings: list[str] = Field(default_factory=list)
