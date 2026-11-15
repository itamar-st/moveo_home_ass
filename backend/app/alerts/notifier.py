"""Outgoing webhook notifier for risk alerts (§8.4)."""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def build_webhook_payload(
    alert: dict[str, Any],
    hub: dict[str, Any],
) -> dict[str, Any]:
    """Format an outgoing webhook payload compatible with Slack incoming webhooks."""
    return {
        "type": "risk_alert",
        "alert": alert,
        "hub": hub,
        "text": f"[{alert.get('alert_type', 'alert')}] {alert.get('message', '')}",
    }


async def send_alert_webhook(
    webhook_url: str,
    alert: dict[str, Any],
    hub: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    max_attempts: int = 3,
    timeout_seconds: float = 5.0,
) -> bool:
    """POST a `risk_alert` payload to `webhook_url` with 5s timeout and up to 2 retries."""
    if not webhook_url:
        return False

    payload = build_webhook_payload(alert, hub)

    async def _attempt(http_client: httpx.AsyncClient) -> bool:
        for attempt in range(1, max_attempts + 1):
            try:
                response = await http_client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout_seconds,
                )
                if 200 <= response.status_code < 300:
                    logger.info(
                        "alert_webhook_sent hub=%s alert_id=%s status=%d",
                        alert.get("hub"),
                        alert.get("id"),
                        response.status_code,
                    )
                    return True
                logger.warning(
                    "alert_webhook_non_2xx attempt=%d/%d status=%d hub=%s",
                    attempt,
                    max_attempts,
                    response.status_code,
                    alert.get("hub"),
                )
                if response.status_code < 500:
                    return False
            except httpx.HTTPError as exc:
                logger.warning(
                    "alert_webhook_error attempt=%d/%d hub=%s error=%s",
                    attempt,
                    max_attempts,
                    alert.get("hub"),
                    exc,
                )
            if attempt < max_attempts:
                await asyncio.sleep(0.25 * attempt)
        return False

    if client is not None:
        return await _attempt(client)

    async with httpx.AsyncClient(timeout=timeout_seconds) as owned_client:
        return await _attempt(owned_client)
