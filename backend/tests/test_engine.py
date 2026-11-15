"""Comprehensive unit tests for WDRI deterministic scoring engine (M3)."""

from datetime import date, timedelta
from typing import Any

import pytest

from app.scoring.config import (
    DEFAULT_SCORING_CONFIG,
    HAZARD_NAMES,
    REQUIRED_NRI_KEYS,
    SCORING_VERSION,
    ScoringConfig,
    get_scoring_metadata,
    tier_for_score,
)
from app.scoring.engine import normalize_metric, score_hub


def _full_nri(**overrides: float | None) -> dict[str, Any]:
    scores: dict[str, float | None] = {k: 50.0 for k in REQUIRED_NRI_KEYS}
    scores.update(overrides)
    return {"version": "NRI December 2025", "county": "Test County", "scores": scores}


def _five_year_baseline(
    *,
    start: date = date(2021, 1, 1),
    end: date = date(2025, 12, 31),
    temp_max_c: float = 20.0,
    temp_min_c: float = 10.0,
    precip_mm: float = 0.0,
    snowfall_cm: float = 0.0,
    gust_max_kmh: float = 20.0,
    weather_code: int = 0,
) -> list[dict[str, Any]]:
    rows = []
    cur = start
    while cur <= end:
        rows.append(
            {
                "date": cur,
                "temp_max_c": temp_max_c,
                "temp_min_c": temp_min_c,
                "precip_mm": precip_mm,
                "snowfall_cm": snowfall_cm,
                "gust_max_kmh": gust_max_kmh,
                "weather_code": weather_code,
            }
        )
        cur += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Normalization & Tiers
# ---------------------------------------------------------------------------


def test_normalize_metric_bounds_and_invalid_cap() -> None:
    assert normalize_metric(-5.0, 10.0) == 0.0
    assert normalize_metric(0.0, 10.0) == 0.0
    assert normalize_metric(5.0, 10.0) == 50.0
    assert normalize_metric(10.0, 10.0) == 100.0
    assert normalize_metric(25.0, 10.0) == 100.0
    with pytest.raises(ValueError, match="cap must be > 0"):
        normalize_metric(5.0, 0.0)


@pytest.mark.parametrize(
    ("score", "expected_tier"),
    [
        (0.0, "Low"),
        (24.99, "Low"),
        (25.0, "Moderate"),
        (49.99, "Moderate"),
        (50.0, "High"),
        (74.99, "High"),
        (75.0, "Severe"),
        (100.0, "Severe"),
    ],
)
def test_tier_boundaries(score: float, expected_tier: str) -> None:
    assert tier_for_score(score) == expected_tier


# ---------------------------------------------------------------------------
# Formula correctness on hand-built fixtures
# ---------------------------------------------------------------------------


def test_score_hub_exact_hand_calculated_fixture() -> None:
    """Verify every hazard score, WDRI overall score, and contribution matches §6 formulas."""
    cfg = ScoringConfig(
        caps={
            "sig_snow_days": 10.0,
            "hard_freeze_days": 20.0,
            "freezing_rain_days": 5.0,
            "heavy_precip_days": 10.0,
            "high_wind_days": 10.0,
            "extreme_heat_days": 20.0,
        }
    )
    rows = _five_year_baseline()
    # Over 5 years (2021-2025):
    # 25 sig snow days -> 5.0/yr -> norm = 50
    for i in range(25):
        rows[i]["snowfall_cm"] = 3.0
    # 50 hard freeze days -> 10.0/yr -> norm = 50
    for i in range(50):
        rows[i]["temp_min_c"] = -12.0
    # 5 freezing rain days -> 1.0/yr -> norm = 20
    for i in range(5):
        rows[i]["weather_code"] = 66
    # 25 heavy precip days -> 5.0/yr -> norm = 50
    for i in range(100, 125):
        rows[i]["precip_mm"] = 30.0
    # 10 high wind days -> 2.0/yr -> norm = 20
    for i in range(200, 210):
        rows[i]["gust_max_kmh"] = 85.0
    # 50 extreme heat days -> 10.0/yr -> norm = 50
    for i in range(300, 350):
        rows[i]["temp_max_c"] = 40.0

    nri = _full_nri(
        winter_weather=80.0,
        ice_storm=60.0,
        cold_wave=40.0,
        hurricane=70.0,
        riverine_flood=60.0,
        coastal_flood=90.0,
        tornado=80.0,
        hail=50.0,
        strong_wind=40.0,
        heat_wave=75.0,
    )

    res = score_hub(
        rows,
        nri,
        start_date=date(2021, 1, 1),
        end_date=date(2025, 12, 31),
        config=cfg,
    )

    # Hand calculations:
    # winter: obs = 0.5*50 + 0.3*50 + 0.2*20 = 25 + 15 + 4 = 44
    #         nri = 0.5*80 + 0.3*60 + 0.2*40 = 40 + 18 + 8 = 66
    #         winter = 0.6*44 + 0.4*66 = 26.4 + 26.4 = 52.8
    # hurricane: 0.8*70 + 0.2*20 = 56 + 4 = 60.0
    # flood: 0.5*50 + 0.5*max(60, 90) = 25 + 45 = 70.0
    # severe_storm: nri_s = 0.4*80 + 0.3*50 + 0.3*40 = 32 + 15 + 12 = 59
    #               severe_storm = 0.8*59 + 0.2*20 = 47.2 + 4 = 51.2
    # heat: 0.6*50 + 0.4*75 = 30 + 30 = 60.0
    hazards = res.details["hazards"]
    assert hazards["winter"]["score"] == 52.8
    assert hazards["hurricane"]["score"] == 60.0
    assert hazards["flood"]["score"] == 70.0
    assert hazards["severe_storm"]["score"] == 51.2
    assert hazards["heat"]["score"] == 60.0

    # weighted = 0.25*52.8 + 0.25*70.0 + 0.20*60.0 + 0.20*51.2 + 0.10*60.0
    #          = 13.2 + 17.5 + 12.0 + 10.24 + 6.0 = 58.94
    # max = 70.0 (flood)
    # WDRI = 0.7*58.94 + 0.3*70.0 = 41.258 + 21.0 = 62.258 -> 62.26
    assert res.overall_score == 62.26
    assert res.overall_tier == "High"
    assert res.operational_score == 62.26
    assert res.confidence == "high"
    assert res.details["scoring_version"] == SCORING_VERSION

    contrib_sum = sum(hazards[h]["contribution"] for h in HAZARD_NAMES)
    assert contrib_sum == pytest.approx(res.overall_score, abs=0.02)
    # Top hazard (flood) gets 0.3 * 70.0 = 21.0 extra contribution
    assert hazards["flood"]["contribution"] == round(0.7 * 0.25 * 70.0 + 0.3 * 70.0, 2)


def test_contributions_sum_to_wdri_even_with_tied_top_hazards() -> None:
    """When all hazards tie, the 0.3*max term is attributed once so contributions sum to WDRI."""
    rows = _five_year_baseline()
    nri = _full_nri(
        winter_weather=50.0,
        ice_storm=50.0,
        cold_wave=50.0,
        hurricane=25.0,
        riverine_flood=40.0,
        coastal_flood=40.0,
        tornado=25.0,
        hail=25.0,
        strong_wind=25.0,
        heat_wave=50.0,
    )
    # All hazard scores equal 20.0 (winter=0.4*50=20, hurricane=0.8*25=20,
    # flood=0.5*40=20, severe_storm=0.8*25=20, heat=0.4*50=20)
    res = score_hub(rows, nri, start_date=date(2021, 1, 1), end_date=date(2025, 12, 31))
    assert res.overall_score == 20.0
    contrib_sum = sum(res.details["hazards"][h]["contribution"] for h in HAZARD_NAMES)
    assert contrib_sum == pytest.approx(res.overall_score, abs=0.01)


def test_zero_inputs_produce_zero_scores_and_low_tier() -> None:
    rows = _five_year_baseline()
    nri = _full_nri(**{k: 0.0 for k in REQUIRED_NRI_KEYS})
    res = score_hub(rows, nri, start_date=date(2021, 1, 1), end_date=date(2025, 12, 31))
    assert res.overall_score == 0.0
    assert res.overall_tier == "Low"
    assert res.operational_score == 0.0
    assert res.details["drivers"] == []


# ---------------------------------------------------------------------------
# Monotonicity tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "high_value", "affected_hazard"),
    [
        ("snowfall_cm", 5.0, "winter"),
        ("temp_min_c", -15.0, "winter"),
        ("weather_code", 66, "winter"),
        ("precip_mm", 40.0, "flood"),
        ("gust_max_kmh", 95.0, "hurricane"),
        ("gust_max_kmh", 95.0, "severe_storm"),
        ("temp_max_c", 42.0, "heat"),
    ],
)
def test_observed_metric_monotonicity(
    field: str, high_value: float, affected_hazard: str
) -> None:
    """Adding more severe weather days never decreases the affected hazard score or WDRI."""
    nri = _full_nri()
    base_rows = _five_year_baseline()
    base_res = score_hub(base_rows, nri, start_date=date(2021, 1, 1), end_date=date(2025, 12, 31))

    prev_hazard = base_res.details["hazards"][affected_hazard]["score"]
    prev_wdri = base_res.overall_score

    for count in (5, 25, 80, 200):
        rows = _five_year_baseline()
        for i in range(count):
            rows[i][field] = high_value
        res = score_hub(rows, nri, start_date=date(2021, 1, 1), end_date=date(2025, 12, 31))
        cur_hazard = res.details["hazards"][affected_hazard]["score"]
        assert cur_hazard >= prev_hazard
        assert res.overall_score >= prev_wdri
        prev_hazard = cur_hazard
        prev_wdri = res.overall_score


@pytest.mark.parametrize("nri_key", REQUIRED_NRI_KEYS)
def test_nri_monotonicity(nri_key: str) -> None:
    """Increasing any FEMA NRI percentile never decreases overall WDRI."""
    rows = _five_year_baseline()
    prev = 0.0
    for val in (0.0, 25.0, 50.0, 75.0, 100.0):
        res = score_hub(
            rows,
            _full_nri(**{nri_key: val}),
            start_date=date(2021, 1, 1),
            end_date=date(2025, 12, 31),
        )
        assert res.overall_score >= prev
        prev = res.overall_score


# ---------------------------------------------------------------------------
# Operational alerts, confidence, and caveats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alerts", "expected_bump"),
    [
        ([], 0.0),
        ([{"event": "Wind Advisory", "severity": "Minor"}], 5.0),
        ([{"event": "Heat Advisory", "severity": "Moderate"}], 10.0),
        ([{"event": "Tornado Watch", "severity": "Severe"}], 20.0),
        ([{"event": "Hurricane Warning", "severity": "Extreme"}], 30.0),
        (
            [
                {"event": "Wind Advisory", "severity": "Minor"},
                {"event": "Tornado Warning", "severity": "Severe"},
            ],
            20.0,
        ),
        ([{"event": "Special Weather Statement", "severity": "Unknown"}], 0.0),
    ],
)
def test_operational_overlay_alert_bumps(alerts: list[dict], expected_bump: float) -> None:
    rows = _five_year_baseline()
    nri = _full_nri()
    base = score_hub(rows, nri, [], start_date=date(2021, 1, 1), end_date=date(2025, 12, 31))
    with_alerts = score_hub(
        rows, nri, alerts, start_date=date(2021, 1, 1), end_date=date(2025, 12, 31)
    )
    assert with_alerts.overall_score == base.overall_score
    assert with_alerts.live_bump == expected_bump
    assert with_alerts.operational_score == round(
        min(100.0, base.overall_score + expected_bump), 2
    )


def test_operational_score_capped_at_100() -> None:
    rows = _five_year_baseline(
        snowfall_cm=20.0, temp_min_c=-20.0, precip_mm=50.0, gust_max_kmh=100.0, temp_max_c=45.0
    )
    nri = _full_nri(**{k: 99.0 for k in REQUIRED_NRI_KEYS})
    res = score_hub(
        rows,
        nri,
        [{"event": "Hurricane Warning", "severity": "Extreme"}],
        start_date=date(2021, 1, 1),
        end_date=date(2025, 12, 31),
    )
    assert res.overall_score > 80.0
    assert res.operational_score == 100.0
    assert res.operational_tier == "Severe"


def test_confidence_levels_and_caveats() -> None:
    start, end = date(2021, 1, 1), date(2025, 12, 31)
    full_rows = _five_year_baseline(start=start, end=end)

    # High confidence even when Not-Applicable nulls are present (e.g. inland coastal_flood=None)
    res_high = score_hub(
        full_rows,
        _full_nri(coastal_flood=None, hurricane=None),
        start_date=start,
        end_date=end,
    )
    assert res_high.confidence == "high"
    assert any("not applicable" in c.lower() for c in res_high.details["caveats"])

    # Medium confidence: 1 missing NRI key
    nri_one_missing = _full_nri()
    del nri_one_missing["scores"]["hail"]
    res_med_nri = score_hub(full_rows, nri_one_missing, start_date=start, end_date=end)
    assert res_med_nri.confidence == "medium"
    assert any("Missing FEMA NRI fields" in c for c in res_med_nri.details["caveats"])

    # Medium confidence: 85% coverage with complete NRI
    rows_85pct = full_rows[: int(len(full_rows) * 0.85)]
    res_med_cov = score_hub(rows_85pct, _full_nri(), start_date=start, end_date=end)
    assert res_med_cov.confidence == "medium"
    assert any("coverage" in c.lower() for c in res_med_cov.details["caveats"])

    # Low confidence: < 80% coverage
    rows_50pct = full_rows[: int(len(full_rows) * 0.50)]
    res_low_cov = score_hub(rows_50pct, _full_nri(), start_date=start, end_date=end)
    assert res_low_cov.confidence == "low"

    # Low confidence: missing NRI payload completely
    res_no_nri = score_hub(
        full_rows,
        None,
        start_date=start,
        end_date=end,
        extra_caveats=["Custom caveat", "Custom caveat"],
    )
    assert res_no_nri.confidence == "low"
    assert res_no_nri.details["caveats"].count("Custom caveat") == 1


def test_scoring_metadata_consistent_with_default_config() -> None:
    meta = get_scoring_metadata(DEFAULT_SCORING_CONFIG)
    assert meta["scoring_version"] == "wdri-v1"
    assert sum(meta["weights"]["hazards"].values()) == pytest.approx(1.0)
    assert meta["caps"] == DEFAULT_SCORING_CONFIG.caps
    assert len(meta["tiers"]) == 4
    assert len(meta["data_sources"]) == 3
    assert len(meta["assumptions"]) >= 4
