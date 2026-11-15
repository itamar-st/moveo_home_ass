"""Pydantic argument and result models for the 6 deterministic agent tools."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Region = Literal[
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

WeatherMetric = Literal[
    "snowfall_days",
    "sig_snow_days",
    "hard_freeze_days",
    "freezing_rain_days",
    "heavy_precip_days",
    "high_wind_days",
    "extreme_heat_days",
    "total_snowfall_cm",
    "total_precip_mm",
]

WeatherPeriod = Literal[
    "last_year",
    "last_365_days",
    "year_to_date",
    "last_5_years",
    "custom",
]

AlertSeverity = Literal["Minor", "Moderate", "Severe", "Extreme"]

ArtifactType = Literal[
    "ranking_table",
    "comparison",
    "score_breakdown",
    "stat",
    "alerts",
]


class ListHubsArgs(BaseModel):
    """Arguments for `list_hubs`."""

    region: Region | None = None


class RankHubsArgs(BaseModel):
    """Arguments for `rank_hubs`."""

    hazard: HazardFilter = "overall"
    region: Region | None = None
    top_n: int = Field(default=5, ge=1, le=20)
    score_kind: ScoreKind = "structural"


class CompareHubsArgs(BaseModel):
    """Arguments for `compare_hubs`."""

    hubs: list[str] = Field(min_length=2, max_length=5)
    hazards: list[HazardFilter] | None = None


class ExplainHubRiskArgs(BaseModel):
    """Arguments for `explain_hub_risk`."""

    hub: str = Field(min_length=1)


class GetWeatherStatsArgs(BaseModel):
    """Arguments for `get_weather_stats`."""

    hub: str = Field(min_length=1)
    metric: WeatherMetric
    period: WeatherPeriod = "last_year"
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_custom_dates(self) -> "GetWeatherStatsArgs":
        """Require valid start_date and end_date when period='custom'."""
        if self.period == "custom":
            if self.start_date is None or self.end_date is None:
                raise ValueError("start_date and end_date are required when period='custom'")
            if self.start_date > self.end_date:
                raise ValueError("start_date must be on or before end_date")
        return self


class GetActiveAlertsArgs(BaseModel):
    """Arguments for `get_active_alerts`."""

    hub: str | None = None
    min_severity: AlertSeverity | None = None


class ToolResult(BaseModel):
    """Standardized output returned by every deterministic tool."""

    ok: bool
    tool_name: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    data_window: list[str] | None = None


class Artifact(BaseModel):
    """Deterministic UI artifact derived strictly from tool results."""

    type: ArtifactType
    title: str
    data: dict[str, Any]


class ToolTraceItem(BaseModel):
    """Execution record for a single tool invocation within a chat turn."""

    step: int
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    ok: bool
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    duration_ms: int = 0
