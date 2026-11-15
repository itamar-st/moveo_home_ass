"""Pure functions for daily weather aggregation, period resolution, and statistics."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from app.scoring.config import DEFAULT_SCORING_CONFIG, ScoringConfig

PeriodName = Literal["last_year", "last_365_days", "year_to_date", "last_5_years", "custom"]
StatMetricName = Literal[
    "snowfall_days",
    "sig_snow_days",
    "hard_freeze_days",
    "freezing_rain_days",
    "heavy_precip_days",
    "high_wind_days",
    "extreme_heat_days",
    "total_snowfall_cm",
    "total_precip_mm",
]


@dataclass(frozen=True)
class AnnualMetrics:
    """Annualized observed weather frequencies and coverage over a window."""

    start_date: date
    end_date: date
    expected_days: int
    observed_days: int
    coverage_pct: float
    sig_snow_days: float
    hard_freeze_days: float
    freezing_rain_days: float
    heavy_precip_days: float
    high_wind_days: float
    extreme_heat_days: float

    def as_dict(self) -> dict[str, float]:
        """Return the 6 annualized metric rates as a dict."""
        return {
            "sig_snow_days": self.sig_snow_days,
            "hard_freeze_days": self.hard_freeze_days,
            "freezing_rain_days": self.freezing_rain_days,
            "heavy_precip_days": self.heavy_precip_days,
            "high_wind_days": self.high_wind_days,
            "extreme_heat_days": self.extreme_heat_days,
        }


@dataclass(frozen=True)
class PeriodStatResult:
    """Deterministic statistic over a resolved date range (used by `get_weather_stats`)."""

    metric: StatMetricName
    value: float
    days_matching: int
    days_total: int
    percentage: float | None
    threshold: str
    start_date: date
    end_date: date
    coverage_pct: float


def _get_field(row: Mapping[str, Any] | Any, name: str) -> Any:
    """Extract field from either a Mapping or an ORM/dataclass object."""
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _to_float(value: Any) -> float | None:
    """Convert numeric/Decimal field to float, preserving None."""
    if value is None:
        return None
    return float(value)


def climatology_window(
    reference_date: date | None = None,
    *,
    years: int = 5,
) -> tuple[date, date]:
    """Return `[start_date, end_date]` for the last `years` full calendar years."""
    if years < 1:
        raise ValueError("years must be >= 1")
    ref = reference_date or date.today()
    last_full_year = ref.year - 1
    start = date(last_full_year - years + 1, 1, 1)
    end = date(last_full_year, 12, 31)
    return start, end


def resolve_period(
    period: PeriodName,
    *,
    reference_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    years: int = 5,
) -> tuple[date, date]:
    """Resolve a named or custom period into concrete `(start_date, end_date)` bounds."""
    ref = reference_date or date.today()
    if period == "last_year":
        y = ref.year - 1
        return date(y, 1, 1), date(y, 12, 31)
    if period == "last_365_days":
        return ref - timedelta(days=364), ref
    if period == "year_to_date":
        return date(ref.year, 1, 1), ref
    if period == "last_5_years":
        return climatology_window(ref, years=years)
    if period == "custom":
        if start_date is None or end_date is None:
            raise ValueError("custom period requires both start_date and end_date")
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")
        return start_date, end_date
    raise ValueError(f"Unsupported period: {period}")


def _filter_window_rows(
    rows: Sequence[Mapping[str, Any] | Any],
    start_date: date,
    end_date: date,
) -> list[Mapping[str, Any] | Any]:
    """Filter and deduplicate daily rows to `[start_date, end_date]` sorted by date."""
    by_date: dict[date, Mapping[str, Any] | Any] = {}
    for row in rows:
        raw_date = _get_field(row, "date")
        if raw_date is None:
            continue
        row_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date
        if start_date <= row_date <= end_date:
            by_date[row_date] = row
    return [by_date[d] for d in sorted(by_date)]


def _is_observed_row(row: Mapping[str, Any] | Any) -> bool:
    """Return True if at least one core weather observation field is non-null."""
    return any(
        _get_field(row, field) is not None
        for field in ("temp_max_c", "temp_min_c", "precip_mm", "snowfall_cm", "gust_max_kmh")
    )


def _nominal_years(start_date: date, end_date: date, expected_days: int) -> float:
    """Compute nominal year count (exact integer when aligned to full calendar years)."""
    if (
        start_date.month == 1
        and start_date.day == 1
        and end_date.month == 12
        and end_date.day == 31
    ):
        return float(end_date.year - start_date.year + 1)
    return expected_days / 365.25


def compute_annual_metrics(
    rows: Sequence[Mapping[str, Any] | Any],
    *,
    start_date: date,
    end_date: date,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> AnnualMetrics:
    """Aggregate daily rows into annualized hazard metric rates and coverage."""
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    expected_days = (end_date - start_date).days + 1
    window_rows = _filter_window_rows(rows, start_date, end_date)
    observed_rows = [r for r in window_rows if _is_observed_row(r)]
    observed_days = len(observed_rows)
    coverage_pct = round(min((observed_days / expected_days) * 100.0, 100.0), 2)

    if observed_days == 0:
        return AnnualMetrics(
            start_date=start_date,
            end_date=end_date,
            expected_days=expected_days,
            observed_days=0,
            coverage_pct=0.0,
            sig_snow_days=0.0,
            hard_freeze_days=0.0,
            freezing_rain_days=0.0,
            heavy_precip_days=0.0,
            high_wind_days=0.0,
            extreme_heat_days=0.0,
        )

    nominal_yrs = _nominal_years(start_date, end_date, expected_days)

    def _annual_rate(field_name: str, predicate) -> float:
        valid_count = 0
        match_count = 0
        for r in observed_rows:
            val = _get_field(r, field_name)
            if val is None:
                continue
            valid_count += 1
            if predicate(val):
                match_count += 1
        if valid_count == 0:
            return 0.0
        effective_years = nominal_yrs * (valid_count / expected_days)
        if effective_years <= 0:
            return 0.0
        return round(match_count / effective_years, 2)

    sig_snow = _annual_rate(
        "snowfall_cm", lambda v: float(v) >= config.sig_snow_cm
    )
    hard_freeze = _annual_rate(
        "temp_min_c", lambda v: float(v) <= config.hard_freeze_c
    )
    freezing_rain = _annual_rate(
        "weather_code", lambda v: int(v) in config.freezing_rain_codes
    )
    heavy_precip = _annual_rate(
        "precip_mm", lambda v: float(v) >= config.heavy_precip_mm
    )
    high_wind = _annual_rate(
        "gust_max_kmh", lambda v: float(v) >= config.high_wind_kmh
    )
    extreme_heat = _annual_rate(
        "temp_max_c", lambda v: float(v) >= config.extreme_heat_c
    )

    return AnnualMetrics(
        start_date=start_date,
        end_date=end_date,
        expected_days=expected_days,
        observed_days=observed_days,
        coverage_pct=coverage_pct,
        sig_snow_days=sig_snow,
        hard_freeze_days=hard_freeze,
        freezing_rain_days=freezing_rain,
        heavy_precip_days=heavy_precip,
        high_wind_days=high_wind,
        extreme_heat_days=extreme_heat,
    )


def compute_period_stat(
    rows: Sequence[Mapping[str, Any] | Any],
    metric: StatMetricName,
    *,
    start_date: date,
    end_date: date,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> PeriodStatResult:
    """Compute a deterministic weather statistic over `[start_date, end_date]`."""
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    expected_days = (end_date - start_date).days + 1
    window_rows = _filter_window_rows(rows, start_date, end_date)
    observed_rows = [r for r in window_rows if _is_observed_row(r)]
    observed_days = len(observed_rows)
    coverage_pct = round(min((observed_days / expected_days) * 100.0, 100.0), 2)

    if metric in ("total_snowfall_cm", "total_precip_mm"):
        field_name = "snowfall_cm" if metric == "total_snowfall_cm" else "precip_mm"
        threshold = "sum over observed days (cm)" if metric == "total_snowfall_cm" else "sum over observed days (mm)"
        vals = [_to_float(_get_field(r, field_name)) for r in observed_rows]
        non_null = [v for v in vals if v is not None]
        total = round(sum(non_null), 2)
        days_matching = sum(1 for v in non_null if v > 0)
        return PeriodStatResult(
            metric=metric,
            value=total,
            days_matching=days_matching,
            days_total=len(non_null),
            percentage=None,
            threshold=threshold,
            start_date=start_date,
            end_date=end_date,
            coverage_pct=coverage_pct,
        )

    day_metric_specs: dict[str, tuple[str, Any, str]] = {
        "snowfall_days": (
            "snowfall_cm",
            lambda v: float(v) >= config.snowfall_day_cm,
            f"snowfall_cm >= {config.snowfall_day_cm}",
        ),
        "sig_snow_days": (
            "snowfall_cm",
            lambda v: float(v) >= config.sig_snow_cm,
            f"snowfall_cm >= {config.sig_snow_cm}",
        ),
        "hard_freeze_days": (
            "temp_min_c",
            lambda v: float(v) <= config.hard_freeze_c,
            f"temp_min_c <= {config.hard_freeze_c}",
        ),
        "freezing_rain_days": (
            "weather_code",
            lambda v: int(v) in config.freezing_rain_codes,
            f"weather_code in {sorted(config.freezing_rain_codes)}",
        ),
        "heavy_precip_days": (
            "precip_mm",
            lambda v: float(v) >= config.heavy_precip_mm,
            f"precip_mm >= {config.heavy_precip_mm}",
        ),
        "high_wind_days": (
            "gust_max_kmh",
            lambda v: float(v) >= config.high_wind_kmh,
            f"gust_max_kmh >= {config.high_wind_kmh}",
        ),
        "extreme_heat_days": (
            "temp_max_c",
            lambda v: float(v) >= config.extreme_heat_c,
            f"temp_max_c >= {config.extreme_heat_c}",
        ),
    }

    if metric not in day_metric_specs:
        raise ValueError(f"Unsupported metric: {metric}")

    field_name, predicate, threshold_desc = day_metric_specs[metric]
    days_total = 0
    days_matching = 0
    for r in observed_rows:
        val = _get_field(r, field_name)
        if val is None:
            continue
        days_total += 1
        if predicate(val):
            days_matching += 1

    pct = round((days_matching / days_total) * 100.0, 2) if days_total > 0 else 0.0
    return PeriodStatResult(
        metric=metric,
        value=float(days_matching),
        days_matching=days_matching,
        days_total=days_total,
        percentage=pct,
        threshold=threshold_desc,
        start_date=start_date,
        end_date=end_date,
        coverage_pct=coverage_pct,
    )
