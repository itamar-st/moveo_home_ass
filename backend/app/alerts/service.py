"""Orchestration service for rescoring hubs, detecting alerts, broadcasting SSE, and webhooks."""

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.broadcaster import AlertBroadcaster, alert_broadcaster
from app.alerts.detector import detect_and_persist_alerts
from app.alerts.notifier import send_alert_webhook
from app.alerts.simulated import get_active_simulated_alerts
from app.config import Settings, get_settings
from app.db.models import Hub, RiskAlert, RiskSnapshot
from app.ingestion.pipeline import refresh_weather_all_hubs
from app.scoring.config import tier_for_score
from app.scoring.runner import rescore_all_hubs

logger = logging.getLogger(__name__)

_rescore_lock = asyncio.Lock()


def serialize_alert(alert: RiskAlert, hub_code: str) -> dict[str, Any]:
    """Serialize a RiskAlert ORM row into the §8.1 RiskAlert JSON shape."""
    created = alert.created_at
    created_str = created.isoformat() + ("Z" if created.tzinfo is None else "")
    return {
        "id": alert.id,
        "hub": hub_code,
        "alert_type": alert.alert_type,
        "old_score": float(alert.old_score) if alert.old_score is not None else None,
        "new_score": float(alert.new_score),
        "message": alert.message,
        "created_at": created_str,
    }


def serialize_hub_with_snapshot(hub: Hub, snapshot: RiskSnapshot | None) -> dict[str, Any]:
    """Serialize a Hub + RiskSnapshot into the §8.1 Hub JSON shape."""
    overall = float(snapshot.overall_score) if snapshot is not None else None
    operational = float(snapshot.operational_score) if snapshot is not None else None
    return {
        "code": hub.code,
        "name": f"{hub.city} Hub",
        "city": hub.city,
        "state": hub.state,
        "region": hub.region,
        "latitude": float(hub.latitude),
        "longitude": float(hub.longitude),
        "county_fips": hub.county_fips,
        "overall_score": overall,
        "overall_tier": tier_for_score(overall) if overall is not None else None,
        "operational_score": operational,
        "operational_tier": tier_for_score(operational) if operational is not None else None,
    }


async def run_rescore_and_alerts(
    session: AsyncSession,
    *,
    refresh_nws: bool = True,
    refresh_weather: bool = False,
    settings: Settings | None = None,
    broadcaster: AlertBroadcaster | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Execute optional weather refresh, deterministic rescore, alert detection, SSE & webhook."""
    cfg = settings or get_settings()
    bus = broadcaster or alert_broadcaster

    async with _rescore_lock:
        weather_summary: dict[str, Any] | None = None
        if refresh_weather:
            weather_summary = await refresh_weather_all_hubs(session, client=http_client)

        sim_alerts = get_active_simulated_alerts()
        rescore_result = await rescore_all_hubs(
            session,
            fetch_nws=refresh_nws,
            simulated_alerts_by_hub=sim_alerts,
            client=http_client,
        )

        hubs_by_id: dict[int, Hub] = rescore_result["hubs_by_id"]
        prev_snapshots: dict[int, RiskSnapshot] = rescore_result["previous_snapshots"]
        new_snapshots: list[RiskSnapshot] = rescore_result["snapshots"]
        new_snap_by_hub = {snap.hub_id: snap for snap in new_snapshots}

        created_alerts = await detect_and_persist_alerts(
            session,
            hubs_by_id,
            prev_snapshots,
            new_snapshots,
            score_delta_threshold=cfg.alert_score_delta,
        )

        bus.publish(
            "scoring_run",
            {
                "computed_at": rescore_result["computed_at"],
                "hubs_scored": rescore_result["hubs_scored"],
                "alerts_created": len(created_alerts),
            },
        )

        serialized_alerts: list[dict[str, Any]] = []
        for alert in created_alerts:
            hub = hubs_by_id[alert.hub_id]
            alert_payload = serialize_alert(alert, hub.code)
            serialized_alerts.append(alert_payload)
            bus.publish("risk_alert", alert_payload)

            if cfg.alert_webhook_url:
                hub_payload = serialize_hub_with_snapshot(hub, new_snap_by_hub.get(hub.id))
                await send_alert_webhook(
                    cfg.alert_webhook_url,
                    alert_payload,
                    hub_payload,
                    client=http_client,
                )

        logger.info(
            "rescore_and_alerts_finished computed_at=%s hubs_scored=%d alerts_created=%d",
            rescore_result["computed_at"],
            rescore_result["hubs_scored"],
            len(created_alerts),
        )

        return {
            "computed_at": rescore_result["computed_at"],
            "hubs_scored": rescore_result["hubs_scored"],
            "nws_failures": rescore_result["nws_failures"],
            "alerts_created": len(created_alerts),
            "alerts": serialized_alerts,
            "weather_refresh": weather_summary,
        }
