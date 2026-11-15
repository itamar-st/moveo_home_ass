"""Deterministic alert detection comparing consecutive RiskSnapshots per hub."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub, RiskAlert, RiskSnapshot
from app.scoring.config import tier_for_score

SEVERE_ALERT_LEVELS = frozenset({"Severe", "Extreme"})


def _alert_signature(alert: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(alert.get("id") or ""),
        str(alert.get("event") or ""),
        str(alert.get("severity") or ""),
    )


def _find_new_severe_alerts(
    prev_details: Mapping[str, Any] | None,
    new_details: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    prev_alerts = (prev_details or {}).get("active_alerts") or []
    new_alerts = (new_details or {}).get("active_alerts") or []

    prev_sigs = {_alert_signature(a) for a in prev_alerts}
    prev_event_sev = {
        (str(a.get("event") or ""), str(a.get("severity") or "")) for a in prev_alerts
    }

    added: list[Mapping[str, Any]] = []
    for alert in new_alerts:
        severity = str(alert.get("severity") or "")
        if severity not in SEVERE_ALERT_LEVELS:
            continue
        sig = _alert_signature(alert)
        ev_sev = (str(alert.get("event") or ""), severity)
        if sig not in prev_sigs and ev_sev not in prev_event_sev:
            added.append(alert)
    return added


def _format_alert_context(alerts: Sequence[Mapping[str, Any]]) -> str:
    if not alerts:
        return ""
    top = alerts[0]
    event = str(top.get("event") or "Weather Alert")
    severity = str(top.get("severity") or "")
    if severity:
        return f" ({event}, {severity})"
    return f" ({event})"


def detect_alert_for_hub(
    hub: Hub,
    prev_snapshot: RiskSnapshot | None,
    new_snapshot: RiskSnapshot,
    *,
    score_delta_threshold: float = 10.0,
) -> RiskAlert | None:
    """Compare `new_snapshot` against `prev_snapshot` and return a `RiskAlert` if triggered."""
    if prev_snapshot is None:
        return None

    old_overall = float(prev_snapshot.overall_score)
    new_overall = float(new_snapshot.overall_score)
    old_op = float(prev_snapshot.operational_score)
    new_op = float(new_snapshot.operational_score)

    old_op_tier = tier_for_score(old_op)
    new_op_tier = tier_for_score(new_op)
    old_overall_tier = tier_for_score(old_overall)
    new_overall_tier = tier_for_score(new_overall)

    new_severe = _find_new_severe_alerts(prev_snapshot.details, new_snapshot.details)
    all_new_alerts = (new_snapshot.details or {}).get("active_alerts") or []
    context = _format_alert_context(new_severe or all_new_alerts)

    alert_type: str | None = None
    old_score_val = old_op
    new_score_val = new_op
    message = ""

    if old_op_tier != new_op_tier:
        alert_type = "tier_change"
        message = f"{hub.code} operational risk {old_op_tier} \u2192 {new_op_tier}{context}"
    elif old_overall_tier != new_overall_tier:
        alert_type = "tier_change"
        old_score_val = old_overall
        new_score_val = new_overall
        message = f"{hub.code} structural risk {old_overall_tier} \u2192 {new_overall_tier}{context}"
    elif abs(new_op - old_op) >= score_delta_threshold:
        alert_type = "score_delta"
        delta = new_op - old_op
        sign = "+" if delta >= 0 else ""
        message = (
            f"{hub.code} operational score {old_op:.1f} \u2192 {new_op:.1f} "
            f"({sign}{delta:.1f}){context}"
        )
    elif new_severe:
        alert_type = "new_severe_weather"
        top = new_severe[0]
        event = str(top.get("event") or "Weather Alert")
        severity = str(top.get("severity") or "Severe")
        message = f"{hub.code} new {severity} alert: {event} (operational {new_op:.1f})"

    if alert_type is None:
        return None

    return RiskAlert(
        hub_id=hub.id,
        alert_type=alert_type,
        old_score=Decimal(f"{old_score_val:.2f}"),
        new_score=Decimal(f"{new_score_val:.2f}"),
        message=message[:255],
        created_at=datetime.now(UTC).replace(tzinfo=None, microsecond=0),
    )


async def detect_and_persist_alerts(
    session: AsyncSession,
    hubs_by_id: Mapping[int, Hub],
    prev_snapshots: Mapping[int, RiskSnapshot],
    new_snapshots: Sequence[RiskSnapshot],
    *,
    score_delta_threshold: float = 10.0,
) -> list[RiskAlert]:
    """Run alert detection across a batch of new snapshots and persist any triggered alerts."""
    created: list[RiskAlert] = []
    for new_snap in new_snapshots:
        hub = hubs_by_id.get(new_snap.hub_id)
        if hub is None:
            continue
        prev_snap = prev_snapshots.get(new_snap.hub_id)
        alert = detect_alert_for_hub(
            hub,
            prev_snap,
            new_snap,
            score_delta_threshold=score_delta_threshold,
        )
        if alert is not None:
            session.add(alert)
            created.append(alert)

    if created:
        await session.commit()
        for alert in created:
            await session.refresh(alert)

    return created
