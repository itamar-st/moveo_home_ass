"""Comprehensive tests for M3 scoring metrics, period resolution, and statistics."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.scoring.config import DEFAULT_SCORING_CONFIG, ScoringConfig
from app.scoring.metrics import (
    climatology_window,
    compute_annual_metrics,
    compute_period_stat,
    resolve_period,
)


def _make_day(
    d: date,
    *,
    temp_max_c: float | None = 15.0,
    temp_min_c: float | None = 5.0,
    precip_mm: float | None = 0.0,
    snowfall_cm: float | None = 0.0,
    gust_max_kmh: float | None = 20.0,
    weather_code: int | None = 0,
) -> dict:
    return {
        "date": d,
        "temp_max_c": temp_max_c,
        "temp_min_c": temp_min_c,
        "precip_mm": precip_mm,
        "snowfall_cm": snowfall_cm,
        "gust_max_kmh": gust_max_kmh,
        "weather_code": weather_code,
    }


def _generate_range(start: date, end: date, **overrides) -> list[dict]:
    rows = []
    cur = start
    while cur <= end:
        rows.append(_make_day(cur, **overrides))
        cur += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Period resolution tests
# ---------------------------------------------------------------------------


def test_climatology_window_default_5_years() -> None:
    start, end = climatology_window(date(2026, 11, 15))
    assert start == date(2021, 1, 1)
    assert end == date(2025, 12, 31)


def test_climatology_window_custom_years() -> None:
    start, end = climatology_window(date(2025, 3, 1), years=3)
    assert start == date(2022, 1, 1)
    assert end == date(2024, 12, 31)


def test_climatology_window_rejects_non_positive_years() -> None:
    with pytest.raises(ValueError, match="years must be >= 1"):
        climatology_window(date(2026, 1, 1), years=0)


def test_resolve_period_last_year() -> None:
    start, end = resolve_period("last_year", reference_date=date(2026, 11, 15))
    assert start == date(2025, 1, 1)
    assert end == date(2025, 12, 31)


def test_resolve_period_last_year_across_leap_year() -> None:
    start, end = resolve_period("last_year", reference_date=date(2025, 2, 10))
    assert start == date(2024, 1, 1)
    assert end == date(2024, 12, 31)
    assert (end - start).days + 1 == 366


def test_resolve_period_last_365_days() -> None:
    ref = date(2026, 11, 15)
    start, end = resolve_period("last_365_days", reference_date=ref)
    assert end == ref
    assert (end - start).days + 1 == 365


def test_resolve_period_year_to_date() -> None:
    ref = date(2026, 11, 15)
    start, end = resolve_period("year_to_date", reference_date=ref)
    assert start == date(2026, 1, 1)
    assert end == ref


def test_resolve_period_last_5_years() -> None:
    start, end = resolve_period("last_5_years", reference_date=date(2026, 6, 1))
    assert start == date(2021, 1, 1)
    assert end == date(2025, 12, 31)


def test_resolve_period_custom_valid_and_invalid() -> None:
    start, end = resolve_period(
        "custom",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 30),
    )
    assert (start, end) == (date(2024, 1, 1), date(2024, 6, 30))

    with pytest.raises(ValueError, match="requires both"):
        resolve_period("custom", start_date=date(2024, 1, 1))

    with pytest.raises(ValueError, match="start_date must be <= end_date"):
        resolve_period("custom", start_date=date(2024, 5, 2), end_date=date(2024, 5, 1))


def test_resolve_period_invalid_name() -> None:
    with pytest.raises(ValueError, match="Unsupported period"):
        resolve_period("next_month", reference_date=date(2026, 1, 1))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Annual metrics & threshold counting tests
# ---------------------------------------------------------------------------


def test_compute_annual_metrics_exact_thresholds_over_5_years() -> None:
    """Inclusive/exclusive boundaries are evaluated precisely across 5 full years."""
    start, end = date(2021, 1, 1), date(2025, 12, 31)
    rows = _generate_range(start, end)

    # Set exact boundary values on specific days (10 days each -> 2.00/yr over 5 years)
    for idx in range(10):
        rows[idx]["snowfall_cm"] = Decimal("2.50")  # >= 2.5 counts
    for idx in range(10, 15):
        rows[idx]["snowfall_cm"] = Decimal("2.49")  # below threshold

    for idx in range(20, 30):
        rows[idx]["temp_min_c"] = Decimal("-10.0")  # <= -10.0 counts
    for idx in range(30, 35):
        rows[idx]["temp_min_c"] = Decimal("-9.9")  # above threshold

    for idx, code in enumerate((56, 57, 66, 67, 56), start=40):
        rows[idx]["weather_code"] = code  # 5 freezing rain days -> 1.00/yr
    rows[45]["weather_code"] = 61  # plain rain -> does not count

    for idx in range(50, 60):
        rows[idx]["precip_mm"] = Decimal("25.00")  # >= 25.0 counts
    rows[60]["precip_mm"] = Decimal("24.99")

    for idx in range(70, 75):
        rows[idx]["gust_max_kmh"] = Decimal("80.0")  # >= 80.0 counts
    rows[75]["gust_max_kmh"] = Decimal("79.9")

    for idx in range(80, 100):
        rows[idx]["temp_max_c"] = Decimal("37.8")  # >= 37.8 counts -> 4.00/yr
    rows[100]["temp_max_c"] = Decimal("37.7")

    metrics = compute_annual_metrics(rows, start_date=start, end_date=end)
    assert metrics.expected_days == 1826
    assert metrics.observed_days == 1826
    assert metrics.coverage_pct == 100.0
    assert metrics.sig_snow_days == 2.0
    assert metrics.hard_freeze_days == 2.0
    assert metrics.freezing_rain_days == 1.0
    assert metrics.heavy_precip_days == 2.0
    assert metrics.high_wind_days == 1.0
    assert metrics.extreme_heat_days == 4.0


def test_compute_annual_metrics_empty_rows() -> None:
    metrics = compute_annual_metrics([], start_date=date(2021, 1, 1), end_date=date(2025, 12, 31))
    assert metrics.observed_days == 0
    assert metrics.coverage_pct == 0.0
    assert metrics.sig_snow_days == 0.0
    assert metrics.hard_freeze_days == 0.0
    assert metrics.freezing_rain_days == 0.0
    assert metrics.heavy_precip_days == 0.0
    assert metrics.high_wind_days == 0.0
    assert metrics.extreme_heat_days == 0.0


def test_compute_annual_metrics_missing_days_scaling_and_coverage() -> None:
    """Missing days reduce coverage_pct and scale observed rates fairly."""
    start, end = date(2021, 1, 1), date(2025, 12, 31)
    all_rows = _generate_range(start, end)
    # Keep only 50% of days (every even index) -> 913 days out of 1826
    half_rows = all_rows[::2]
    # Put 10 significant snow days in the observed half
    for i in range(10):
        half_rows[i]["snowfall_cm"] = 5.0

    metrics = compute_annual_metrics(half_rows, start_date=start, end_date=end)
    assert metrics.expected_days == 1826
    assert metrics.observed_days == 913
    assert metrics.coverage_pct == 50.0
    # 10 events across 2.5 effective years -> 4.0/yr
    assert metrics.sig_snow_days == 4.0


def test_compute_annual_metrics_ignores_all_null_rows_and_out_of_window_rows() -> None:
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    rows = _generate_range(date(2024, 12, 20), date(2026, 1, 10), snowfall_cm=10.0)
    # Null out core fields for 5 days inside 2025
    for r in rows:
        if date(2025, 6, 1) <= r["date"] <= date(2025, 6, 5):
            r["temp_max_c"] = None
            r["temp_min_c"] = None
            r["precip_mm"] = None
            r["snowfall_cm"] = None
            r["gust_max_kmh"] = None
            r["weather_code"] = None

    metrics = compute_annual_metrics(rows, start_date=start, end_date=end)
    assert metrics.expected_days == 365
    assert metrics.observed_days == 360
    assert metrics.coverage_pct == round(360 / 365 * 100, 2)
    assert metrics.sig_snow_days == 365.0


def test_compute_annual_metrics_deduplicates_dates_and_accepts_orm_objects() -> None:
    class RowObj:
        def __init__(self, d: date, snowfall_cm: float) -> None:
            self.date = d
            self.temp_max_c = 1.0
            self.temp_min_c = -1.0
            self.precip_mm = 1.0
            self.snowfall_cm = snowfall_cm
            self.gust_max_kmh = 10.0
            self.weather_code = 71

    rows = [
        RowObj(date(2025, 1, 1), 5.0),
        RowObj(date(2025, 1, 1), 0.0),  # duplicate date overrides earlier entry
        RowObj(date(2025, 1, 2), 3.0),
    ]
    metrics = compute_annual_metrics(rows, start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
    assert metrics.observed_days == 2
    assert metrics.coverage_pct == 100.0
    # 1 snow day out of 2 days -> annualized = 1 / (2 / 365.25) = 182.62 (or .63)
    assert metrics.sig_snow_days == pytest.approx(182.63, abs=0.02)


def test_compute_annual_metrics_partial_null_field_does_not_crash() -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 10)
    rows = _generate_range(start, end, weather_code=None)
    rows[0]["snowfall_cm"] = None
    metrics = compute_annual_metrics(rows, start_date=start, end_date=end)
    assert metrics.freezing_rain_days == 0.0
    assert metrics.sig_snow_days == 0.0


def test_compute_annual_metrics_rejects_inverted_window() -> None:
    with pytest.raises(ValueError, match="start_date must be <= end_date"):
        compute_annual_metrics([], start_date=date(2025, 2, 1), end_date=date(2025, 1, 1))


# ---------------------------------------------------------------------------
# Period weather stat tests (for Q3 / get_weather_stats)
# ---------------------------------------------------------------------------


def test_compute_period_stat_snowfall_days_percentage_math() -> None:
    """Q3-style percentage math: 0.1 cm threshold vs 2.5 cm significant snow threshold."""
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    rows = _generate_range(start, end)
    # 36 days with trace/measurable snow >= 0.1 cm, 10 of which are >= 2.5 cm
    for i in range(36):
        rows[i]["snowfall_cm"] = 0.2 if i >= 10 else 4.0

    stat_all = compute_period_stat(rows, "snowfall_days", start_date=start, end_date=end)
    assert stat_all.days_matching == 36
    assert stat_all.days_total == 365
    assert stat_all.percentage == round(36 / 365 * 100, 2)  # 9.86%
    assert stat_all.coverage_pct == 100.0
    assert "0.1" in stat_all.threshold

    stat_sig = compute_period_stat(rows, "sig_snow_days", start_date=start, end_date=end)
    assert stat_sig.days_matching == 10
    assert stat_sig.percentage == round(10 / 365 * 100, 2)  # 2.74%


@pytest.mark.parametrize(
    ("metric", "field", "matching_val", "non_matching_val"),
    [
        ("hard_freeze_days", "temp_min_c", -12.0, -5.0),
        ("freezing_rain_days", "weather_code", 66, 61),
        ("heavy_precip_days", "precip_mm", 30.0, 10.0),
        ("high_wind_days", "gust_max_kmh", 85.0, 40.0),
        ("extreme_heat_days", "temp_max_c", 40.0, 32.0),
    ],
)
def test_compute_period_stat_all_day_metrics(
    metric: str, field: str, matching_val: float, non_matching_val: float
) -> None:
    start, end = date(2025, 7, 1), date(2025, 7, 10)
    rows = _generate_range(start, end, **{field: non_matching_val})
    rows[0][field] = matching_val
    rows[1][field] = matching_val
    rows[2][field] = None  # missing value excluded from days_total

    stat = compute_period_stat(rows, metric, start_date=start, end_date=end)  # type: ignore[arg-type]
    assert stat.days_matching == 2
    assert stat.days_total == 9
    assert stat.percentage == round(2 / 9 * 100, 2)


def test_compute_period_stat_totals_and_empty() -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 5)
    rows = _generate_range(start, end, snowfall_cm=2.5, precip_mm=10.0)
    rows[4]["snowfall_cm"] = 0.0

    snow_total = compute_period_stat(rows, "total_snowfall_cm", start_date=start, end_date=end)
    assert snow_total.value == 10.0
    assert snow_total.days_matching == 4
    assert snow_total.days_total == 5
    assert snow_total.percentage is None

    precip_total = compute_period_stat(rows, "total_precip_mm", start_date=start, end_date=end)
    assert precip_total.value == 50.0
    assert precip_total.days_matching == 5

    empty_stat = compute_period_stat([], "snowfall_days", start_date=start, end_date=end)
    assert empty_stat.days_matching == 0
    assert empty_stat.days_total == 0
    assert empty_stat.percentage == 0.0
    assert empty_stat.coverage_pct == 0.0


def test_compute_period_stat_custom_config_thresholds() -> None:
    cfg = ScoringConfig(extreme_heat_c=35.0)
    start, end = date(2025, 8, 1), date(2025, 8, 4)
    rows = _generate_range(start, end, temp_max_c=36.0)
    stat_custom = compute_period_stat(
        rows, "extreme_heat_days", start_date=start, end_date=end, config=cfg
    )
    stat_default = compute_period_stat(
        rows,
        "extreme_heat_days",
        start_date=start,
        end_date=end,
        config=DEFAULT_SCORING_CONFIG,
    )
    assert stat_custom.days_matching == 4
    assert stat_default.days_matching == 0
