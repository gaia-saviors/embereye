"""FIRMS CSV parsers -> canonical records.

Strict, total, deterministic: a malformed field rejects the row (counted,
never silently dropped); a valid row always normalizes to the same values.
Column sets match NASA FIRMS text exports:

MODIS C6.1 : latitude longitude brightness scan track acq_date acq_time
             satellite instrument confidence version bright_t31 frp daynight
VIIRS 375m : latitude longitude bright_ti4 scan track acq_date acq_time
             satellite instrument confidence version bright_ti5 frp daynight
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Iterable

from ..ingest.canonical import (
    MODIS_BAND21_LIMIT_K,
    MODIS_BAND22_SATURATION_K,
    SENSOR_MODIS,
    SENSOR_VIIRS,
    VIIRS_I4_SATURATION_K,
    CanonicalRecord,
    RecordRejected,
    canonical_satellite,
    local_calendar_day,
    ordinalize_confidence,
    parse_acq_time,
    pixel_area_km2,
)

REQUIRED_MODIS = {"latitude", "longitude", "brightness", "acq_date", "acq_time",
                  "satellite", "confidence", "frp", "daynight"}
REQUIRED_VIIRS = {"latitude", "longitude", "bright_ti4", "acq_date", "acq_time",
                  "satellite", "confidence", "frp", "daynight"}


def _f(row: dict, key: str, default: float | None = None) -> float | None:
    raw = (row.get(key) or "").strip()
    if raw == "":
        return default
    return float(raw)


def _parse_date(raw: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    raise RecordRejected(f"bad acq_date: {raw!r}")


def parse_firms_csv(
    text: str, aoi_id: str, source_hint: str = "", product: str = "NRT"
) -> tuple[list[CanonicalRecord], int, int, list[str]]:
    """Parse one FIRMS CSV export.

    Returns (records, n_rejected, n_total, rejection_samples).
    `source_hint` is used when the CSV carries no `instrument` column.
    `product` tags each record with its FIRMS product era: "SP" (standard
    science quality archive, includes the `type` column) or "NRT"
    (near-real-time, no `type` column -> firms_type stays None).
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], 0, 0, ["empty csv"]
    cols = {c.strip().lower(): c for c in reader.fieldnames}
    header_map = {cols[c]: c for c in cols}
    norm_row_iter = (
        {k.lower(): (v or "") for k, v in row.items() if k is not None}
        for row in reader
    )

    lower_fields = set(cols.keys())
    if {"brightness", "bright_t31"} & lower_fields:
        sensor_family, required = SENSOR_MODIS, REQUIRED_MODIS
    elif {"bright_ti4", "bright_ti5"} & lower_fields:
        sensor_family, required = SENSOR_VIIRS, REQUIRED_VIIRS
    else:
        return [], 0, 0, [f"unrecognized header: {sorted(cols)[:8]}..."]
    missing = required - lower_fields
    if missing:
        return [], 0, 0, [f"missing columns: {sorted(missing)}"]

    records: list[CanonicalRecord] = []
    rejected = 0
    total = 0
    samples: list[str] = []

    for row in norm_row_iter:
        total += 1
        try:
            rec = _normalize_row(row, aoi_id, sensor_family, header_map,
                                 source_hint, product)
            records.append(rec)
        except (RecordRejected, ValueError, TypeError) as exc:
            rejected += 1
            if len(samples) < 10:
                samples.append(str(exc))
    return records, rejected, total, samples


def _normalize_row(
    row: dict, aoi_id: str, sensor_family: str, header_map: dict, source_hint: str,
    product: str = "NRT",
) -> CanonicalRecord:
    lat = float(row["latitude"])
    lon = float(row["longitude"])
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise RecordRejected(f"lat/lon out of range: {lat},{lon}")
    if lat == 0.0 and lon == 0.0:
        raise RecordRejected("null-island sentinel row")

    day = _parse_date(row["acq_date"]).date()
    hh, mm = parse_acq_time(row["acq_time"])
    acq_dt = datetime(day.year, day.month, day.day, hh, mm)

    satellite = canonical_satellite(row["satellite"])
    instrument = (row.get("instrument") or "").strip() or source_hint or sensor_family

    if sensor_family == SENSOR_MODIS:
        t4 = _f(row, "brightness")
        t11 = _f(row, "bright_t31")
        resolution = 1000
    else:
        t4 = _f(row, "bright_ti4")
        t11 = _f(row, "bright_ti5")
        resolution = 375

    conf_class, conf_raw = ordinalize_confidence(row["confidence"], sensor_family)

    daynight = (row.get("daynight") or "").strip().upper()
    if daynight.startswith("D"):
        daynight = "D"
    elif daynight.startswith("N"):
        daynight = "N"
    else:
        raise RecordRejected(f"bad daynight: {row.get('daynight')!r}")

    frp = _f(row, "frp")
    if frp is not None and frp < 0:
        frp = None  # FIRMS emits 0/negative sentinels occasionally; treat as missing

    scan = _f(row, "scan", 1.0) or 1.0
    track = _f(row, "track", 1.0) or 1.0
    area = pixel_area_km2(scan, track)

    saturated = False
    band21_fallback = False
    if sensor_family == SENSOR_VIIRS and t4 is not None:
        saturated = t4 >= VIIRS_I4_SATURATION_K
    elif sensor_family == SENSOR_MODIS and t4 is not None:
        band21_fallback = t4 >= MODIS_BAND22_SATURATION_K
        saturated = t4 >= MODIS_BAND21_LIMIT_K

    version = (row.get("version") or "").strip()

    # Optional FIRMS `type` column: present in SP exports, absent in NRT.
    firms_type: int | None = None
    type_raw = (row.get("type") or "").strip()
    if type_raw:
        try:
            firms_type = int(float(type_raw))
        except ValueError as exc:
            raise RecordRejected(f"bad type token: {type_raw!r}") from exc

    return CanonicalRecord(
        aoi_id=aoi_id,
        sensor_family=sensor_family,
        satellite=satellite,
        instrument=instrument,
        resolution_m=resolution,
        acq_dt_utc=acq_dt,
        acq_date=day,
        local_date=local_calendar_day(acq_dt, lon),
        lat=lat,
        lon=lon,
        brightness_t4_k=t4,
        brightness_t11_k=t11,
        frp_mw=frp,
        confidence_class=conf_class,
        confidence_raw=conf_raw,
        daynight=daynight,
        scan_km=scan,
        track_km=track,
        pixel_area_km2=area,
        saturated=saturated,
        band21_fallback=band21_fallback,
        source_version=version,
        firms_product=product,
        firms_type=firms_type,
        static_source=(firms_type == 2),
    )


def iter_parse(texts: Iterable[tuple[str, str, str]], aoi_id: str):
    """Parse many (text, source_hint) exports into one canonical stream."""
    for text, hint, product in texts:
        recs, rej, tot, _samples = parse_firms_csv(text, aoi_id, hint, product=product)
        yield recs, rej, tot
