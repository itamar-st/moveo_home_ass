"""Unit tests for deterministic risk alert detection (M7)."""

from datetime import datetime
from decimal import Decimal

from app.alerts.detector import detect_alert_for_hub
from app.db.models import Hub, RiskSnapshot


def _make_hub(hub_id: int = 1, code: str = "MIA") -> Hub:
    return Hub(
        id=hub_id,
        code=code,
        city="Miami",
        state="FL",
        region="Southeast",
        latitude=Decimal("25.76170"),
        longitude=Decimal("-80.19180"),
        county_fips="12086",
        timezone="America/New_York",
    )


def _make_snapshot(
    hub_id: int = 1,
    *,
    overall: float = 58.1,
    operational: float = 58.1,
    active_alerts: list[dict] | None = None,
) -> RiskSnapshot:
    return RiskSnapshot(
        hub_id=hub_id,
        computed_at=datetime(2026, 11, 15, 18, 0, 0),
        overall_score=Decimal(f"{overall:.2f}"),
        operational_score=Decimal(f"{operational:.2f}"),
        details={"active_alerts": active_alerts or []},
    )


def test_detector_returns_none_without_previous_snapshot() -> None:
    hub = _make_hub()
    new_snap = _make_snapshot(overall=58.1, operational=88.1)
    assert detect_alert_for_hub(hub, None, new_snap) is None


def test_detector_tier_change_on_operational_score() -> None:
    hub = _make_hub(code="MIA")
    prev_snap = _make_snapshot(overall=58.1, operational=58.1)
    new_snap = _make_snapshot(
        overall=58.1,
        operational=88.1,
        active_alerts=[
            {
                "id": "sim-1",
                "event": "Hurricane Warning",
                "severity": "Extreme",
                "simulated": True,
            }
        ],
    )

    alert = detect_alert_for_hub(hub, prev_snap, new_snap)
    assert alert is not None
    assert alert.alert_type == "tier_change"
    assert float(alert.old_score) == 58.1
    assert float(alert.new_score) == 88.1
    assert "High → Severe" in alert.message
    assert "Hurricane Warning" in alert.message


def test_detector_score_delta_without_tier_change() -> None:
    hub = _make_hub(code="DFW")
    prev_snap = _make_snapshot(overall=52.0, operational=52.0)
    new_snap = _make_snapshot(overall=52.0, operational=64.0)

    alert = detect_alert_for_hub(hub, prev_snap, new_snap, score_delta_threshold=10.0)
    assert alert is not None
    assert alert.alert_type == "score_delta"
    assert float(alert.old_score) == 52.0
    assert float(alert.new_score) == 64.0
    assert "+12.0" in alert.message


def test_detector_new_severe_weather_without_tier_or_delta() -> None:
    hub = _make_hub(code="ORD")
    prev_snap = _make_snapshot(
        overall=65.0,
        operational=70.0,
        active_alerts=[{"id": "a1", "event": "Wind Advisory", "severity": "Minor"}],
    )
    new_snap = _make_snapshot(
        overall=65.0,
        operational=74.0,
        active_alerts=[
            {"id": "a1", "event": "Wind Advisory", "severity": "Minor"},
            {"id": "a2", "event": "Blizzard Warning", "severity": "Severe"},
        ],
    )

    alert = detect_alert_for_hub(hub, prev_snap, new_snap, score_delta_threshold=10.0)
    assert alert is not None
    assert alert.alert_type == "new_severe_weather"
    assert "Blizzard Warning" in alert.message


def test_detector_no_alert_on_unchanged_or_small_delta() -> None:
    hub = _make_hub(code="DEN")
    prev_snap = _make_snapshot(
        overall=40.0,
        operational=45.0,
        active_alerts=[{"id": "a1", "event": "Flood Watch", "severity": "Severe"}],
    )
    new_snap = _make_snapshot(
        overall=40.0,
        operational=48.0,
        active_alerts=[{"id": "a1", "event": "Flood Watch", "severity": "Severe"}],
    )

    assert detect_alert_for_hub(hub, prev_snap, new_snap, score_delta_threshold=10.0) is None
