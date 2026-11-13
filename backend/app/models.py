"""EmberEye relational model.

Design notes
------------
* Every column is dialect-portable (PostgreSQL 16 in production, SQLite in
  dev/CI). Enumerations are stored as validated strings.
* `Hotspot` is the canonical normalized detection record: one row per sensor
  detection, already AOI-assigned at ingest time (presets) and spatially
  addressable (lat/lon index) for user-drawn AOIs.
* `HarmonizedDaily` is the calendar substrate: per AOI-day fused metrics with
  per-day provenance, seam weight and precomputed anomaly flags.
* Aggregation results are materialized, never recomputed per request: the
  platform is query-light by design (Volume 5 performance budget).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Area of interest
# --------------------------------------------------------------------------
class Aoi(Base):
    __tablename__ = "aois"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    # GeoJSON Feature geometry (Polygon). Stored as JSON text for portability.
    geojson: Mapped[str] = mapped_column(Text)
    west: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    north: Mapped[float] = mapped_column(Float)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north


# --------------------------------------------------------------------------
# Ingest provenance
# --------------------------------------------------------------------------
class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16))          # LIVE | SEED
    source: Mapped[str] = mapped_column(String(48))        # firms_api | synthetic | firmware
    aoi_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("aois.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16))        # RUNNING | OK | PARTIAL | FAILED
    message: Mapped[str] = mapped_column(Text, default="")
    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_kept: Mapped[int] = mapped_column(Integer, default=0)
    rows_deduped: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")


# --------------------------------------------------------------------------
# Canonical hotspot record
# --------------------------------------------------------------------------
class Hotspot(Base):
    __tablename__ = "hotspots"

    # BigInteger with an Integer variant on SQLite: SQLite only autoincrements
    # INTEGER PRIMARY KEY columns.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    aoi_id: Mapped[str] = mapped_column(String(64), ForeignKey("aois.id"))

    sensor_family: Mapped[str] = mapped_column(String(8))   # MODIS | VIIRS
    satellite: Mapped[str] = mapped_column(String(16))      # Terra | Aqua | SNPP | N20 | N21
    instrument: Mapped[str] = mapped_column(String(16))     # MODIS | VIIRS
    resolution_m: Mapped[int] = mapped_column(Integer)      # 1000 | 375

    acq_dt_utc: Mapped[datetime] = mapped_column(DateTime)
    acq_date: Mapped[date] = mapped_column(Date)
    # Local calendar day (AOI solar-time bucketing, lon/15h) - aggregation grain.
    local_date: Mapped[date] = mapped_column(Date)

    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)

    brightness_t4_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    brightness_t11_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    frp_mw: Mapped[float | None] = mapped_column(Float, nullable=True)

    confidence_class: Mapped[str] = mapped_column(String(8))  # LOW | NOMINAL | HIGH
    confidence_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    daynight: Mapped[str] = mapped_column(String(1))          # D | N

    scan_km: Mapped[float] = mapped_column(Float, default=1.0)
    track_km: Mapped[float] = mapped_column(Float, default=1.0)
    pixel_area_km2: Mapped[float] = mapped_column(Float, default=1.0)

    saturated: Mapped[bool] = mapped_column(Boolean, default=False)
    band21_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    # PRIMARY static definition in the SP era: FIRMS type=2 (incl. NRT rows
    # matched to SP-learned type=2 locations). Flag - never deleted.
    static_source: Mapped[bool] = mapped_column(Boolean, default=False)
    # SECONDARY tag (Phase 2): the 60-day/T4-stability persistence mask.
    # Static sources stay in totals (challenge text: they are burning
    # activity); this tag powers the "recurring source" cross-tab.
    recurring_source: Mapped[bool] = mapped_column(Boolean, default=False)

    source_version: Mapped[str] = mapped_column(String(32), default="")
    # FIRMS product era: "SP" (standard archive) | "NRT" (near-real-time).
    # "" for synthetic seed rows. The SP/NRT seam is provisional (see Methods).
    firms_product: Mapped[str] = mapped_column(String(8), default="")
    # Raw FIRMS `type` column (0 vegetation, 1 volcano, 2 other static land
    # source); NULL when the product has no type column (all NRT rows).
    firms_type: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("ingest_runs.id"))

    __table_args__ = (
        Index("ix_hotspot_aoi_date", "aoi_id", "local_date"),
        Index("ix_hotspot_aoi_sensor_date", "aoi_id", "sensor_family", "local_date"),
        Index("ix_hotspot_aoi_sat_date", "aoi_id", "satellite", "local_date"),
        Index("ix_hotspot_latlon", "lat", "lon"),
    )


# --------------------------------------------------------------------------
# Cross-sensor calibration
# --------------------------------------------------------------------------
class CalibrationModel(Base):
    __tablename__ = "calibration_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aoi_id: Mapped[str] = mapped_column(String(64), ForeignKey("aois.id"))
    variable: Mapped[str] = mapped_column(String(16))       # COUNTS | FRP
    direction: Mapped[str] = mapped_column(String(24))      # MODIS_TO_VIIRS
    method: Mapped[str] = mapped_column(String(24))         # theil_sen_log1p
    slope: Mapped[float] = mapped_column(Float)
    intercept: Mapped[float] = mapped_column(Float)         # log-space intercept
    r2_in_sample: Mapped[float] = mapped_column(Float)   # log space
    r2_oos: Mapped[float] = mapped_column(Float)         # log space, chronological holdout
    r2_raw_oos: Mapped[float] = mapped_column(Float, default=0.0)  # raw scale holdout
    n_months: Mapped[int] = mapped_column(Integer)
    n_pairs: Mapped[int] = mapped_column(Integer)           # raw paired detections
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    # Cross-sensor agreement over the overlap era (the "built-in lie
    # detector", Volume 5): min Spearman rho between the fused series and
    # each sensor's series through the fitted mapping. Stored once per AOI
    # on the COUNTS row; 0.0 otherwise.
    agreement_stat: Mapped[float] = mapped_column(Float, default=0.0)
    params_json: Mapped[str] = mapped_column(Text, default="{}")  # day/night split, pairs stats
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# Materialized aggregates
# --------------------------------------------------------------------------
class DailyAggregate(Base):
    __tablename__ = "daily_aggregates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aoi_id: Mapped[str] = mapped_column(String(64), ForeignKey("aois.id"))
    local_date: Mapped[date] = mapped_column(Date)
    satellite: Mapped[str] = mapped_column(String(16))
    sensor_family: Mapped[str] = mapped_column(String(8))
    n_detections: Mapped[int] = mapped_column(Integer, default=0)
    n_strict: Mapped[int] = mapped_column(Integer, default=0)   # static-source excluded
    n_day: Mapped[int] = mapped_column(Integer, default=0)
    n_night: Mapped[int] = mapped_column(Integer, default=0)
    frp_sum_mw: Mapped[float] = mapped_column(Float, default=0.0)
    frp_mean_mw: Mapped[float] = mapped_column(Float, default=0.0)
    pixel_area_sum_km2: Mapped[float] = mapped_column(Float, default=0.0)
    t4_mean_k: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        UniqueConstraint("aoi_id", "local_date", "satellite", name="uq_daily_aoi_date_sat"),
        Index("ix_daily_aoi_date", "aoi_id", "local_date"),
    )


class HarmonizedDaily(Base):
    __tablename__ = "harmonized_daily"

    aoi_id: Mapped[str] = mapped_column(String(64), ForeignKey("aois.id"), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)

    # Raw counts, all populations (provenance/compat; NOT the headline).
    n_modis: Mapped[int] = mapped_column(Integer, default=0)
    # Raw VIIRS counts of the REFERENCE platform only (SNPP 2012..2026-11-02,
    # NOAA-20 after, continuity-divided) - N20/N21 overlap is never summed.
    n_viirs: Mapped[int] = mapped_column(Integer, default=0)
    # COMPARABLE SUBSET (Phase 2): daytime, non-static detections. This is
    # the population the cross-sensor transfer is calibrated on.
    n_comparable_modis: Mapped[int] = mapped_column(Integer, default=0)
    n_comparable_viirs: Mapped[int] = mapped_column(Integer, default=0)
    # Headline series: comparable-subset VIIRS-equivalent. Pre-2012 = month-
    # of-year factor x MODIS; from 2012 = native reference VIIRS.
    n_harmonized: Mapped[float] = mapped_column(Float, default=0.0)
    # 95% prediction interval from the leave-one-year-out validation; lo=hi
    # in the native era (no transfer uncertainty).
    n_harmonized_lo: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_harmonized_hi: Mapped[float | None] = mapped_column(Float, nullable=True)

    frp_modis_mw: Mapped[float] = mapped_column(Float, default=0.0)
    frp_viirs_mw: Mapped[float] = mapped_column(Float, default=0.0)
    frp_harmonized_mw: Mapped[float] = mapped_column(Float, default=0.0)
    frp_harmonized_lo_mw: Mapped[float | None] = mapped_column(Float, nullable=True)
    frp_harmonized_hi_mw: Mapped[float | None] = mapped_column(Float, nullable=True)

    # SEPARATE LAYERS from 2012 (no MODIS equivalent exists before 2012, so
    # including them pre-2012 would fake a step change): static-source and
    # non-static night detections of the reference platform. Included in
    # totals only where the layer is switched on; never hidden silently.
    n_static: Mapped[int] = mapped_column(Integer, default=0)
    n_night: Mapped[int] = mapped_column(Integer, default=0)
    # static share of the day's reference-platform burning activity; NULL
    # when the day has no reference-platform detections.
    static_share: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Retired Phase 1 concept; kept as the era indicator 0=MODIS-side,
    # 1=VIIRS-side for schema stability.
    seam_weight: Mapped[float] = mapped_column(Float, default=1.0)
    provenance: Mapped[str] = mapped_column(String(24))
    # MODIS_TRANSFERRED | VIIRS_NATIVE | NO_DATA

    z_n: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_frp: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Leave-one-year-out EMPIRICAL percentile 0-100 (Phase 2 rebuild).
    pct_n: Mapped[float | None] = mapped_column(Float, nullable=True)
    flag_high: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_low: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------
# Fire episodes (spatio-temporal clustering) and seasons
# --------------------------------------------------------------------------
class FireEpisode(Base):
    __tablename__ = "fire_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aoi_id: Mapped[str] = mapped_column(String(64), ForeignKey("aois.id"))
    label: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    duration_days: Mapped[int] = mapped_column(Integer, default=1)
    n_detections: Mapped[int] = mapped_column(Integer, default=0)
    peak_frp_mw: Mapped[float] = mapped_column(Float, default=0.0)
    total_frp_mw: Mapped[float] = mapped_column(Float, default=0.0)
    intensity_index: Mapped[float] = mapped_column(Float, default=0.0)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    bounds_west: Mapped[float] = mapped_column(Float)
    bounds_south: Mapped[float] = mapped_column(Float)
    bounds_east: Mapped[float] = mapped_column(Float)
    bounds_north: Mapped[float] = mapped_column(Float)
    sensor_mix_json: Mapped[str] = mapped_column(Text, default="{}")
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_episode_aoi_start", "aoi_id", "start_date"),)


class SeasonMetrics(Base):
    __tablename__ = "season_metrics"

    aoi_id: Mapped[str] = mapped_column(String(64), ForeignKey("aois.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    onset_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    season_length_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    peak_value: Mapped[float] = mapped_column(Float, default=0.0)
    active_days: Mapped[int] = mapped_column(Integer, default=0)
    max_streak_days: Mapped[int] = mapped_column(Integer, default=0)
    # Season censoring (Phase 2): True for boundary years whose record does
    # not span the full calendar (2000 starts in Nov, 2026 partial, platform
    # start years). Partial seasons are excluded from baseline statistics
    # and marked in the UI - never silently compared against full years.
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------
# Background jobs + system state
# --------------------------------------------------------------------------
class AggregationJob(Base):
    __tablename__ = "aggregation_jobs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    aoi_id: Mapped[str] = mapped_column(String(64), ForeignKey("aois.id"))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING|RUNNING|DONE|FAILED
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
