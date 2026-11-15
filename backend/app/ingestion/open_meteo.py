"""Open-Meteo Archive API client and WeatherDaily upsert helper."""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.db.models import WeatherDaily

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "wind_gusts_10m_max",
    "weather_code",
)
UPSERT_BATCH_SIZE = 500


class RetryableHTTPError(Exception):
    """HTTP error for 429 or 5xx responses that should be retried."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _to_decimal(value: Any, scale: int) -> Decimal | None:
    """Convert a numeric value to a quantized Decimal, preserving None."""
    if value is None:
        return None
    quant = Decimal(10) ** -scale
    return Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)


def parse_archive_daily(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Parse Open-Meteo archive JSON payload into WeatherDaily-ready dicts."""
    daily = payload.get("daily") or {}
    times: list[str] = daily.get("time") or []
    temp_max = daily.get("temperature_2m_max") or []
    temp_min = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_sum") or []
    snowfall = daily.get("snowfall_sum") or []
    gust = daily.get("wind_gusts_10m_max") or []
    weather_code = daily.get("weather_code") or []

    rows: list[dict[str, Any]] = []
    for idx, date_str in enumerate(times):
        raw_wcode = weather_code[idx] if idx < len(weather_code) else None
        rows.append(
            {
                "date": date.fromisoformat(date_str),
                "temp_max_c": _to_decimal(temp_max[idx] if idx < len(temp_max) else None, 1),
                "temp_min_c": _to_decimal(temp_min[idx] if idx < len(temp_min) else None, 1),
                "precip_mm": _to_decimal(precip[idx] if idx < len(precip) else None, 2),
                "snowfall_cm": _to_decimal(snowfall[idx] if idx < len(snowfall) else None, 2),
                "gust_max_kmh": _to_decimal(gust[idx] if idx < len(gust) else None, 1),
                "weather_code": int(raw_wcode) if raw_wcode is not None else None,
            }
        )
    return rows


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception_type(
        (httpx.TransportError, httpx.TimeoutException, RetryableHTTPError)
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _get_archive_json(
    client: httpx.AsyncClient,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Perform GET request to Open-Meteo archive with retry on 429/5xx/network errors."""
    response = await client.get(OPEN_METEO_ARCHIVE_URL, params=params)
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableHTTPError(
            response.status_code,
            f"Open-Meteo returned retryable status {response.status_code}",
        )
    response.raise_for_status()
    return response.json()


async def fetch_archive_daily(
    lat: float | Decimal,
    lon: float | Decimal,
    timezone: str,
    start_date: date | str,
    end_date: date | str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch daily historical weather from Open-Meteo Archive API for one location."""
    start_str = start_date.isoformat() if isinstance(start_date, date) else start_date
    end_str = end_date.isoformat() if isinstance(end_date, date) else end_date

    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "start_date": start_str,
        "end_date": end_str,
        "daily": ",".join(DAILY_FIELDS),
        "timezone": timezone,
    }

    if client is not None:
        payload = await _get_archive_json(client, params)
    else:
        async with httpx.AsyncClient(timeout=30.0) as owned_client:
            payload = await _get_archive_json(owned_client, params)

    return parse_archive_daily(payload)


async def upsert_weather_daily(
    session: AsyncSession,
    hub_id: int,
    rows: Sequence[Mapping[str, Any]],
    *,
    commit: bool = True,
) -> int:
    """Idempotently upsert daily weather rows for a hub into `weather_daily`."""
    if not rows:
        return 0

    bind = session.get_bind()
    dialect_name = bind.dialect.name

    records = [
        {
            "hub_id": hub_id,
            "date": row["date"],
            "temp_max_c": row.get("temp_max_c"),
            "temp_min_c": row.get("temp_min_c"),
            "precip_mm": row.get("precip_mm"),
            "snowfall_cm": row.get("snowfall_cm"),
            "gust_max_kmh": row.get("gust_max_kmh"),
            "weather_code": row.get("weather_code"),
        }
        for row in rows
    ]

    for start in range(0, len(records), UPSERT_BATCH_SIZE):
        chunk = records[start : start + UPSERT_BATCH_SIZE]
        if dialect_name == "mysql":
            stmt = mysql_insert(WeatherDaily).values(chunk)
            stmt = stmt.on_duplicate_key_update(
                temp_max_c=stmt.inserted.temp_max_c,
                temp_min_c=stmt.inserted.temp_min_c,
                precip_mm=stmt.inserted.precip_mm,
                snowfall_cm=stmt.inserted.snowfall_cm,
                gust_max_kmh=stmt.inserted.gust_max_kmh,
                weather_code=stmt.inserted.weather_code,
            )
        else:
            stmt = sqlite_insert(WeatherDaily).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["hub_id", "date"],
                set_={
                    "temp_max_c": stmt.excluded.temp_max_c,
                    "temp_min_c": stmt.excluded.temp_min_c,
                    "precip_mm": stmt.excluded.precip_mm,
                    "snowfall_cm": stmt.excluded.snowfall_cm,
                    "gust_max_kmh": stmt.excluded.gust_max_kmh,
                    "weather_code": stmt.excluded.weather_code,
                },
            )
        await session.execute(stmt)

    if commit:
        await session.commit()

    return len(records)
