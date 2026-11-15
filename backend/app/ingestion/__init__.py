"""External data ingestion clients and orchestration."""

from app.ingestion.fema_nri import apply_nri_to_hubs, load_nri_data
from app.ingestion.nws import fetch_active_alerts
from app.ingestion.open_meteo import fetch_archive_daily, upsert_weather_daily
from app.ingestion.pipeline import fetch_alerts_all_hubs, refresh_weather_all_hubs

__all__ = [
    "apply_nri_to_hubs",
    "fetch_active_alerts",
    "fetch_alerts_all_hubs",
    "fetch_archive_daily",
    "load_nri_data",
    "refresh_weather_all_hubs",
    "upsert_weather_daily",
]
