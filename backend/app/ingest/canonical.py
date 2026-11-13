"""Canonical normalized hotspot record shared by every ingestion path.

The normalizer is the single place where FIRMS' heterogeneous sensor
vocabularies become one deterministic schema:
  * confidence -> three-level ordinal scale (LOW/NOMINAL/HIGH)
  * acquisition time -> UTC datetime + AOI-solar local calendar day
  * pixel geometry -> scan/track km and derived pixel area
  * thermal channels -> unified T4 (mid-IR) / T11 (thermal IR) names
  * saturation physics -> explicit flags (VIIRS I4 @367 K, MODIS band-22
    @331 K with band-21 fallback to ~500 K)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


SENSOR_MODIS = "MODIS"
SENSOR_VIIRS = "VIIRS"

CONFIDENCE_RANK = {"LOW": 0, "NOMINAL": 1, "HIGH": 2}

# VIIRS I4 (3.55-3.93 um) saturation at ~367 K (Schroeder et al. 2014).
VIIRS_I4_SATURATION_K = 367.0
# MODIS band 22 saturates ~331 K; band 21 ("fire channel") covers to ~500 K
# (Giglio et al. 2016). FIRMS `brightness` above 331 K implies band-21 use.
MODIS_BAND22_SATURATION_K = 331.0
MODIS_BAND21_LIMIT_K = 500.0

_SATELLITE_ALIASES = {
    "T": "Terra",
    "A": "Aqua",
    "TERRA": "Terra",
    "AQUA": "Aqua",
    "SNPP": "SNPP",
    "S-NPP": "SNPP",
    "SUOMI NPP": "SNPP",
    "SUOMI-NPP": "SNPP",
    "N": "SNPP",
    "N20": "N20",
    "NOAA-20": "N20",
    "NOAA20": "N20",
    "N21": "N21",
    "NOAA-21": "N21",
    "NOAA21": "N21",
}


@dataclass(slots=True)
class CanonicalRecord:
    """One normalized active-fire detection."""

    aoi_id: str
    sensor_family: str          # MODIS | VIIRS
    satellite: str              # Terra | Aqua | SNPP | N20 | N21
    instrument: str
    resolution_m: int           # 1000 | 375
    acq_dt_utc: datetime
    acq_date: date
    local_date: date
    lat: float
    lon: float
    brightness_t4_k: float | None
    brightness_t11_k: float | None
    frp_mw: float | None
    confidence_class: str       # LOW | NOMINAL | HIGH
    confidence_raw: float | None
    daynight: str               # D | N
    scan_km: float
    track_km: float
    pixel_area_km2: float
    saturated: bool
    band21_fallback: bool
    source_version: str = ""
    run_id: int = 0
    # FIRMS product provenance: "SP" | "NRT" for real FIRMS rows, "" for
    # synthetic seed rows. The SP/NRT seam is provisional by nature (NRT is
    # unreviewed science quality); see METHODOLOGY.md.
    firms_product: str = ""
    # Raw FIRMS `type` column (0 vegetation, 1 volcano, 2 other static land
    # source). NRT exports have no `type` column -> always None there.
    firms_type: int | None = None
    # Ingest-time static-source flag (FIRMS type=2, or NRT row matched to a
    # static location learned from SP type=2 rows).
    static_source: bool = False
    # Ingest-time bookkeeping (never persisted on the hotspot row itself)
    dup_group: int = field(default=-1)


class RecordRejected(ValueError):
    """Raised when a raw row cannot be normalized safely."""


def canonical_satellite(raw: str) -> str:
    key = (raw or "").strip().upper()
    if key in _SATELLITE_ALIASES:
        return _SATELLITE_ALIASES[key]
    raise RecordRejected(f"unknown satellite token: {raw!r}")


def parse_acq_time(raw: str) -> tuple[int, int]:
    """FIRMS acq_time -> (HH, MM).

    SP exports sometimes drop leading zeros (1-4 digits: "0" = 00:00,
    "850" = 08:50); NRT exports carry HHMM; a 6-digit HHMMSS form is also
    accepted (seconds ignored: FIRMS CSV minutes granularity; per-second
    dedup is not attempted)."""
    digits = (raw or "").strip()
    if not digits or not digits.isdigit():
        raise RecordRejected(f"bad acq_time: {raw!r}")
    if len(digits) <= 4:
        digits = digits.zfill(4)
        hh, mm = int(digits[:2]), int(digits[2:])
    elif len(digits) == 6:
        hh, mm = int(digits[:2]), int(digits[2:4])
    else:
        raise RecordRejected(f"bad acq_time: {raw!r}")
    if hh > 23 or mm > 59:
        raise RecordRejected(f"acq_time out of range: {raw!r}")
    return hh, mm


def ordinalize_confidence(raw: str, sensor_family: str) -> tuple[str, float | None]:
    """Map both confidence vocabularies onto the common three-level scale.

    MODIS (NRT CSV): integer 0-100 -> LOW <30, NOMINAL 30-79, HIGH >=80
        (Giglio et al. 2016 classes). SP CSV may emit l/n/h directly.
    VIIRS 375 m: categorical l/n/h -> LOW/NOMINAL/HIGH.
    Returns (class, raw_numeric_or_None). The raw numeric value is preserved
    for MODIS; VIIRS categorical input never receives a fabricated number.
    """
    token = (raw or "").strip().lower()
    if sensor_family == SENSOR_MODIS and token.replace(".", "", 1).isdigit():
        value = float(token)
        if not 0.0 <= value <= 100.0:
            raise RecordRejected(f"MODIS confidence out of range: {raw!r}")
        if value < 30:
            return "LOW", value
        if value < 80:
            return "NOMINAL", value
        return "HIGH", value
    if token in {"l", "low"}:
        return "LOW", None
    if token in {"n", "nominal"}:
        return "NOMINAL", None
    if token in {"h", "high"}:
        return "HIGH", None
    raise RecordRejected(f"unknown confidence token: {raw!r}")


def local_calendar_day(acq_dt_utc: datetime, lon: float) -> date:
    """AOI-solar local calendar day: UTC plus the longitude/15 h offset.

    Satellite overpasses bracket local solar noon/midnight; bucketing by the
    solar day keeps the diurnal cycle intact in daily aggregates without
    requiring per-pixel timezone lookups (documented simplification).
    """
    offset_hours = round(lon / 15.0)
    return (acq_dt_utc + timedelta(hours=offset_hours)).date()


def pixel_area_km2(scan_km: float, track_km: float) -> float:
    return float(max(scan_km, 1e-3) * max(track_km, 1e-3))
