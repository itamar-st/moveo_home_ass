"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the Weather Risk Intelligence Agent backend."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "mysql+asyncmy://wria:wria@localhost:3306/wria"

    # OpenRouter LLM
    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-3.5-super-vl:free"
    openrouter_fallback_models: str = ""
    openrouter_app_url: str = "http://localhost:5173"
    openrouter_app_name: str = "Weather Risk Intelligence Agent"

    # NWS API
    nws_user_agent: str = "WeatherRiskAgent (you@example.com)"

    # Admin & Alerts
    admin_token: str = "change-me-admin-token"
    alert_webhook_url: str = ""
    alert_score_delta: float = 10.0

    # Scheduler
    scheduler_enabled: bool = True
    nws_refresh_minutes: int = 30
    weather_refresh_cron: str = "0 6 * * *"

    # API & Rate Limiting
    cors_origins: str = "http://localhost:5173"
    chat_rate_limit_per_min: int = 10
    max_agent_steps: int = 6

    # Scoring
    scoring_version: str = "wdri_v1"

    # Logging
    log_level: str = "INFO"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a clean list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def fallback_models_list(self) -> list[str]:
        """Parse comma-separated fallback OpenRouter models."""
        return [m.strip() for m in self.openrouter_fallback_models.split(",") if m.strip()]


AVAILABLE_LLM_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "nvidia/nemotron-3.5-super-vl:free",
        "label": "NVIDIA Nemotron 3.5 Super VL",
        "provider": "NVIDIA",
        "description": "Default structured-output model (free tier)",
    },
    {
        "id": "google/gemma-4-31b-it:free",
        "label": "Google Gemma 4 31B",
        "provider": "Google",
        "description": "Gemma 4 31B instruction-tuned model (free tier)",
    },
    {
        "id": "nvidia/nemotron-3.5-lightning:free",
        "label": "NVIDIA Nemotron 3.5 Lightning",
        "provider": "NVIDIA",
        "description": "Low-latency Nemotron 3.5 Lightning model (free tier)",
    },
)

AVAILABLE_LLM_MODEL_IDS: frozenset[str] = frozenset(m["id"] for m in AVAILABLE_LLM_MODELS)


def is_supported_llm_model(model_id: str, settings: Settings | None = None) -> bool:
    """Return True if model_id is in the selectable 3-model catalog or configured default."""
    resolved_settings = settings or get_settings()
    return model_id in AVAILABLE_LLM_MODEL_IDS or model_id == resolved_settings.openrouter_model


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
