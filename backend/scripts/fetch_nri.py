"""Fetch FEMA National Risk Index (NRI) county scores for all seeded hubs.

Usage:
    python -m scripts.fetch_nri
"""

import json
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FEMA_NRI_FEATURE_URL = (
    "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
    "National_Risk_Index_Counties/FeatureServer/0/query"
)
OUT_FIELDS = (
    "STCOFIPS,COUNTY,COUNTYTYPE,NRI_VER,RISK_SCORE,HRCN_RISKS,IFLD_RISKS,"
    "CFLD_RISKS,WNTW_RISKS,ISTM_RISKS,CWAV_RISKS,TRND_RISKS,HAIL_RISKS,SWND_RISKS,HWAV_RISKS"
)

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"
DEFAULT_HUBS_PATH = DATA_DIR / "hubs.json"
DEFAULT_NRI_OUT_PATH = DATA_DIR / "nri_hubs.json"


def _round_score(value: Any) -> float | None:
    """Round a numeric NRI score to 2 decimal places, preserving None."""
    if value is None:
        return None
    return round(float(value), 2)


def transform_nri_feature(attributes: dict[str, Any]) -> dict[str, Any]:
    """Transform a FEMA NRI ArcGIS feature attributes dict into `hubs.nri` JSON shape."""
    county = str(attributes.get("COUNTY") or "").strip()
    county_type = str(attributes.get("COUNTYTYPE") or "").strip()
    if county_type and not county.lower().endswith(county_type.lower()):
        county_label = f"{county} {county_type}".strip()
    else:
        county_label = county

    nri_ver = str(attributes.get("NRI_VER") or "").strip()
    version = f"NRI {nri_ver}" if nri_ver else "NRI"

    # FEMA NRI December 2025 uses IFLD_RISKS (Inland Flooding); older releases used RFLD_RISKS.
    riverine_raw = (
        attributes.get("RFLD_RISKS")
        if attributes.get("RFLD_RISKS") is not None
        else attributes.get("IFLD_RISKS")
    )

    return {
        "version": version,
        "county": county_label,
        "scores": {
            "composite": _round_score(attributes.get("RISK_SCORE")),
            "hurricane": _round_score(attributes.get("HRCN_RISKS")),
            "riverine_flood": _round_score(riverine_raw),
            "coastal_flood": _round_score(attributes.get("CFLD_RISKS")),
            "winter_weather": _round_score(attributes.get("WNTW_RISKS")),
            "ice_storm": _round_score(attributes.get("ISTM_RISKS")),
            "cold_wave": _round_score(attributes.get("CWAV_RISKS")),
            "tornado": _round_score(attributes.get("TRND_RISKS")),
            "hail": _round_score(attributes.get("HAIL_RISKS")),
            "strong_wind": _round_score(attributes.get("SWND_RISKS")),
            "heat_wave": _round_score(attributes.get("HWAV_RISKS")),
        },
    }


def build_nri_hubs_map(
    hub_entries: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Match FEMA NRI features to hub entries by `county_fips` (`STCOFIPS`)."""
    by_fips: dict[str, dict[str, Any]] = {}
    for feature in features:
        attrs = feature.get("attributes") or {}
        fips = str(attrs.get("STCOFIPS") or "").strip()
        if fips:
            by_fips[fips] = attrs

    result: dict[str, dict[str, Any]] = {}
    missing_fips: list[str] = []
    for hub in hub_entries:
        code = hub["code"]
        fips = hub["county_fips"]
        attrs = by_fips.get(fips)
        if attrs is None:
            missing_fips.append(f"{code}:{fips}")
            continue
        result[code] = transform_nri_feature(attrs)

    if missing_fips:
        raise RuntimeError(
            f"Missing FEMA NRI records for hub county_fips: {', '.join(missing_fips)}"
        )
    return result


def fetch_and_write_nri(
    hubs_path: Path = DEFAULT_HUBS_PATH,
    out_path: Path = DEFAULT_NRI_OUT_PATH,
) -> dict[str, dict[str, Any]]:
    """Query FEMA NRI ArcGIS FeatureServer for all hub FIPS and write `nri_hubs.json`."""
    with hubs_path.open("r", encoding="utf-8") as f:
        hub_entries: list[dict[str, Any]] = json.load(f)

    fips_list = [hub["county_fips"] for hub in hub_entries]
    quoted_fips = ",".join(f"'{fips}'" for fips in fips_list)
    params = {
        "where": f"STCOFIPS IN ({quoted_fips})",
        "outFields": OUT_FIELDS,
        "returnGeometry": "false",
        "f": "json",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.get(FEMA_NRI_FEATURE_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    if "error" in payload:
        raise RuntimeError(f"FEMA NRI API error: {payload['error']}")

    features = payload.get("features") or []
    nri_map = build_nri_hubs_map(hub_entries, features)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(nri_map, f, indent=2)
        f.write("\n")

    logger.info("Wrote %d hub NRI records to %s", len(nri_map), out_path)
    return nri_map


def main() -> None:
    """CLI entrypoint for fetching FEMA NRI scores."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    nri_map = fetch_and_write_nri()
    print(f"fetch_nri: wrote {len(nri_map)} hubs to {DEFAULT_NRI_OUT_PATH}")


if __name__ == "__main__":
    main()
