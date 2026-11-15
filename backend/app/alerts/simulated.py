"""In-memory store for simulated NWS alerts with automatic expiry (single-replica demo)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

_SIMULATED_ALERTS: dict[str, list[dict[str, Any]]] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _prune_expired_locked(now: datetime) -> None:
    for code in list(_SIMULATED_ALERTS.keys()):
        active = [a for a in _SIMULATED_ALERTS[code] if a["_expires_at"] > now]
        if active:
            _SIMULATED_ALERTS[code] = active
        else:
            del _SIMULATED_ALERTS[code]


def add_simulated_alert(
    hub_code: str,
    severity: str,
    event: str,
    *,
    duration_hours: float = 6.0,
    headline: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Register an in-memory simulated NWS alert for `hub_code` with expiry."""
    current = now or _utcnow()
    _prune_expired_locked(current)

    normalized_code = hub_code.strip().upper()
    expires_at = current + timedelta(hours=duration_hours)
    entry: dict[str, Any] = {
        "id": f"sim-{uuid4().hex[:12]}",
        "event": event,
        "severity": severity,
        "headline": headline or f"Simulated {severity} {event} for {normalized_code}",
        "effective": current.isoformat(),
        "expires": expires_at.isoformat(),
        "simulated": True,
        "_expires_at": expires_at,
    }
    _SIMULATED_ALERTS.setdefault(normalized_code, []).append(entry)
    return {k: v for k, v in entry.items() if k != "_expires_at"}


def get_active_simulated_alerts(now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """Return unexpired simulated alerts keyed by hub code (strips internal metadata)."""
    current = now or _utcnow()
    _prune_expired_locked(current)
    return {
        code: [{k: v for k, v in item.items() if k != "_expires_at"} for item in items]
        for code, items in _SIMULATED_ALERTS.items()
    }


def clear_simulated_alerts(hub_code: str | None = None) -> int:
    """Clear simulated alerts for one hub or all hubs; return count removed."""
    if hub_code is None:
        count = sum(len(items) for items in _SIMULATED_ALERTS.values())
        _SIMULATED_ALERTS.clear()
        return count

    normalized = hub_code.strip().upper()
    removed = len(_SIMULATED_ALERTS.pop(normalized, []))
    return removed
