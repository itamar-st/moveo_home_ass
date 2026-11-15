"""National Weather Service (NWS) active alerts client."""

import logging
from decimal import Decimal
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"


class RetryableNWSError(Exception):
    """HTTP error for 429 or 5xx responses from NWS API."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def parse_nws_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract normalized alert dicts from an NWS GeoJSON FeatureCollection."""
    features = payload.get("features") or []
    alerts: list[dict[str, Any]] = []
    for feature in features:
        props = (feature or {}).get("properties") or {}
        event = props.get("event")
        if not event:
            continue
        alerts.append(
            {
                "event": event,
                "severity": props.get("severity") or "Unknown",
                "headline": props.get("headline"),
                "expires": props.get("expires"),
            }
        )
    return alerts


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(
        (httpx.TransportError, httpx.TimeoutException, RetryableNWSError)
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _get_nws_json(
    client: httpx.AsyncClient,
    params: dict[str, str],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Perform GET request to NWS alerts API with retries on 429/5xx/network errors."""
    response = await client.get(NWS_ALERTS_URL, params=params, headers=headers)
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableNWSError(
            response.status_code,
            f"NWS API returned retryable status {response.status_code}",
        )
    response.raise_for_status()
    return response.json()


async def fetch_active_alerts_with_status(
    lat: float | Decimal,
    lon: float | Decimal,
    *,
    user_agent: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch active NWS alerts and return `(alerts, ok)`.

    When NWS is unreachable after retries, returns `([], False)` so callers can
    preserve the previous alert state with an explicit caveat.
    """
    ua = user_agent or get_settings().nws_user_agent
    headers = {
        "User-Agent": ua,
        "Accept": "application/geo+json",
    }
    params = {"point": f"{float(lat):.4f},{float(lon):.4f}"}

    try:
        if client is not None:
            payload = await _get_nws_json(client, params, headers)
        else:
            async with httpx.AsyncClient(timeout=15.0) as owned_client:
                payload = await _get_nws_json(owned_client, params, headers)
        return parse_nws_alerts(payload), True
    except (httpx.HTTPError, RetryableNWSError) as exc:
        logger.warning(
            "nws_fetch_failed point=%s error=%s",
            params["point"],
            exc,
        )
        return [], False


async def fetch_active_alerts(
    lat: float | Decimal,
    lon: float | Decimal,
    *,
    user_agent: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch active NWS alerts for a coordinate pair.

    Returns an empty list on transient errors after retries so NWS outages
    never block scoring.
    """
    alerts, _ = await fetch_active_alerts_with_status(
        lat,
        lon,
        user_agent=user_agent,
        client=client,
    )
    return alerts
