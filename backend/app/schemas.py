"""EmberEye API contract (frozen).

These DTOs define the wire format consumed by the frontend. Fields are
additive-stable: new fields may appear, existing fields never change meaning.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

SensorFamily = Literal["MODIS", "VIIRS"]
ConfidenceClass = Literal["LOW", "NOMINAL", "HIGH"]
Provenance = Literal[
    "MODIS_TRANSFERRED",
    "VIIRS_NATIVE",
    "NO_DATA",
    # Legacy values (pre-Phase-2 databases)
    "MODIS_ONLY",
    "OVERLAP_FUSED",
    "VIIRS_FORWARD",
]
DataMode = Literal["AUTO", "SEED", "LIVE"]


# --------------------------------------------------------------------------
# Health / meta
# --------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]
    data_mode: DataMode
    effective_data_mode: Literal["LIVE", "SEED"]
    seed_version: str
    firms: "FirmsStatus"
    counts: "StoreCounts"
    last_ingest: "IngestSummary | None" = None


class FirmsStatus(BaseModel):
    key_configured: bool
    reachable: bool | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


class DataSourceInfo(BaseModel):
    """Data-source badge, DERIVED FROM THE DATA (v1.0.6, handoff P2b).

    Replaces the manual SystemState flag: the badge is computed from what
    the hotspots table actually holds (products, real vs synthetic, first/
    last acquisition) plus the last ingest run. The word LIVE is never
    used unless a real refresh job has run."""
    kind: Literal["FIRMS_ARCHIVE", "SYNTHETIC_SEED", "MIXED", "EMPTY"]
    label: str
    auto_refresh: bool = False
    n_hotspots: int = 0
    products: dict[str, int] = {}
    first_acquisition: date | None = None
    last_acquisition: date | None = None
    last_ingest_at: datetime | None = None
    last_ingest_status: str | None = None
    detail: str = ""


class StoreCounts(BaseModel):
    aois: int
    hotspots: int
    harmonized_days: int
    episodes: int
    coverage_start: date | None = None
    coverage_end: date | None = None


class IngestSummary(BaseModel):
    id: int
    mode: str
    source: str
    status: str
    rows_kept: int
    rows_deduped: int
    rows_rejected: int
    started_at: datetime
    finished_at: datetime | None = None
    message: str = ""


class SensorInfo(BaseModel):
    family: SensorFamily
    satellites: list[str]
    resolution_m: int
    record_start: date
    swath_km: float
    overpass_lst: list[str]
    confidence_scheme: str


class EraInfo(BaseModel):
    name: str
    start: date
    end: date
    description: str


class FirmsSeam(BaseModel):
    """SP/NRT product seam for one satellite: dates where the archive source
    switches from the standard product to near-real-time. Counts produced on
    or after the seam are provisional (NRT is unreviewed science quality)."""
    satellite: str
    sp_last_date: date | None = None
    nrt_first_date: date | None = None
    provisional: bool = True
    note: str = ""


class MetaResponse(BaseModel):
    name: str
    tagline: str
    version: str
    seed_version: str
    data_mode: DataMode
    effective_data_mode: Literal["LIVE", "SEED"]
    # Data-source badge substrate, derived from the data itself (v1.0.6).
    # effective_data_mode stays for API compatibility but the UI badge is
    # built from data_source only - the manual SystemState flag is no
    # longer read.
    data_source: DataSourceInfo
    seed_disclaimer: str | None = None
    sensors: list[SensorInfo]
    eras: list[EraInfo]
    firms_seams: list[FirmsSeam] = []
    baseline: "BaselineInfo"
    methodology: "MethodologySummary"
    citations: list[str]


class BaselineInfo(BaseModel):
    start_year: int
    end_year: int
    kernel_days: int
    z_threshold: float
    definition: str


class MethodologySummary(BaseModel):
    harmonization_tier: str
    steps: list[str]
    units: str


# --------------------------------------------------------------------------
# AOI
# --------------------------------------------------------------------------
class AoiCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    # GeoJSON Polygon geometry (lon/lat rings, first ring = exterior).
    geojson: dict


class AoiOut(BaseModel):
    id: str
    name: str
    description: str
    geojson: dict
    west: float
    south: float
    east: float
    north: float
    is_preset: bool
    created_at: datetime
    # Phase 3A (defect 7): False when this AOI has no harmonized data in the
    # loaded database - the UI disables the entry and labels it
    # "no data loaded in this version" instead of showing empty results.
    has_data: bool = True
    # Job that is currently building this custom AOI, when applicable.
    job_id: str | None = None


class JobOut(BaseModel):
    id: str
    aoi_id: str
    status: Literal["PENDING", "RUNNING", "DONE", "FAILED"]
    progress: float
    message: str
    started_at: datetime
    finished_at: datetime | None = None


# --------------------------------------------------------------------------
# Hotspots (GeoJSON)
# --------------------------------------------------------------------------
class HotspotQuery(BaseModel):
    aoi_id: str
    start: date | None = None
    end: date | None = None
    sensor: Literal["all", "MODIS", "VIIRS"] = "all"
    satellite: str | None = None
    confidence: Literal["all", "LOW", "NOMINAL", "HIGH"] = "all"
    daynight: Literal["all", "D", "N"] = "all"
    min_frp_mw: float | None = None
    limit: int = Field(default=5000, ge=1, le=20000)
    offset: int = Field(default=0, ge=0)


class HotspotFeatureProps(BaseModel):
    id: int
    sensor: SensorFamily
    satellite: str
    acq_dt_utc: datetime
    local_date: date
    frp_mw: float | None
    t4_k: float | None
    t11_k: float | None
    confidence: ConfidenceClass
    daynight: Literal["D", "N"]
    pixel_area_km2: float
    saturated: bool
    band21_fallback: bool
    static_source: bool


class HotspotCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[dict]
    meta: "CollectionMeta"


class CollectionMeta(BaseModel):
    total: int
    returned: int
    offset: int
    truncated: bool


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------
class CalendarCell(BaseModel):
    date: date
    year: int
    doy: int
    n_modis: int
    n_viirs: int
    n_harmonized: float
    n_harmonized_lo: float | None = None
    n_harmonized_hi: float | None = None
    n_comparable_modis: int = 0
    n_comparable_viirs: int = 0
    n_static: int = 0
    n_night: int = 0
    static_share: float | None = None
    frp_modis_mw: float
    frp_viirs_mw: float
    frp_harmonized_mw: float
    z_n: float | None = None
    z_frp: float | None = None
    pct_n: float | None = None
    flag_high: bool = False
    flag_low: bool = False
    seam_weight: float = 0.0
    provenance: Provenance


class SeasonOut(BaseModel):
    year: int
    onset_date: date | None
    end_date: date | None
    season_length_days: int | None
    peak_date: date | None
    peak_value: float
    active_days: int
    max_streak_days: int
    is_partial: bool = False


class TransferredWeek(BaseModel):
    """One ISO-week cell of the transferred (MODIS) era, Phase 3A defect 3.

    The 2000-2011 daily values are a x7 scaling of 0-3 MODIS counts -
    mostly quantization noise at daily level. The calendar shows this era
    as WEEKLY aggregates with the anomaly test applied AT the shown
    aggregation (leave-one-year-out empirical percentiles over transferred-
    era weeks only), so pre/post-2012 rows stop answering different
    questions. Marked estimated everywhere it is drawn."""
    year: int
    iso_week: int
    start_date: date
    end_date: date
    n_harmonized: float
    n_harmonized_lo: float | None = None
    n_harmonized_hi: float | None = None
    pct_n: float | None = None
    flag_high: bool = False
    flag_low: bool = False
    n_days_with_data: int = 0


class WeeklyCell(BaseModel):
    """STEP B: ONE weekly cell per (year, ISO week) for the WHOLE record.

    The calendar's default grid is weekly everywhere, on ONE scale and ONE
    unit (detections per week), so 2000-2011 and 2012+ rows finally answer
    the same question. The transferred-era rows carry exactly the values
    and week-level anomaly flags of TransferredWeek (same math, same
    guards); the VIIRS-native rows are weekly SUMS of the stored daily
    harmonized series with the week-level LOYO test applied inside their
    own era only. `provisional` marks weeks that contain NRT product rows
    (near-real-time, still revisable)."""
    year: int
    iso_week: int
    start_date: date
    end_date: date
    n_harmonized: float
    n_harmonized_lo: float | None = None
    n_harmonized_hi: float | None = None
    pct_n: float | None = None
    flag_high: bool = False
    flag_low: bool = False
    n_days_with_data: int = 0
    provenance: str = "VIIRS_NATIVE"
    provisional: bool = False


class CalendarFinding(BaseModel):
    """The data-verified finding stated in the Calendar headline sentence.

    Computed server-side from the same weekly series the grid draws, so
    the title can never drift from the picture. `scripts/phase3a_evidence.py`
    recomputes all four numbers straight from the database as a check."""
    annual_total: float
    # Share of all harmonized detections whose week-midpoint (Wednesday)
    # falls in September or October.
    sep_oct_share: float
    # The same share restricted to each era - the TWO baselines of the
    # headline sentence (estimated transferred era vs measured VIIRS era).
    sep_oct_share_transferred: float | None = None
    sep_oct_share_native: float | None = None
    # Smallest contiguous ISO-week-of-year window (pooled over all years,
    # grown outward from the peak week) containing >= 80% of activity.
    w80_start_week: int
    w80_end_week: int
    w80_weeks_span: int
    # The 2018 daytime drop, quantified here so the annotation on the grid
    # is data-driven too (AUDIT_REAL A6: real, cause open, documented).
    y2018_daytime_change_pct: float | None = None


class CalendarResponse(BaseModel):
    aoi_id: str
    baseline: BaselineInfo
    cells: list[CalendarCell]
    seasons: list[SeasonOut]
    summary: "CalendarSummary"
    # Phase 3A: seam date (first VIIRS-native day) + weekly transferred-era
    # cells. Years < viirs_start_date are drawn from transferred_weeks;
    # their stored DAILY anomaly flags are suppressed in the UI (they were
    # computed at a level the noise dominates - defect 3).
    viirs_start_date: date | None = None
    transferred_weeks: list[TransferredWeek] = []
    # STEP B: weekly cells for the WHOLE record (single scale/unit grid)
    # + the headline finding computed from that same series.
    weekly_cells: list[WeeklyCell] = []
    finding: "CalendarFinding | None" = None


class CalendarSummary(BaseModel):
    max_n: float
    max_frp: float
    n_anomaly_high: int
    n_anomaly_low: int
    years: list[int]
    # Phase 3A: anomaly counts actually displayed - daily flags in the
    # VIIRS-native era + weekly flags in the transferred era.
    n_anomaly_high_native: int = 0
    n_anomaly_low_native: int = 0
    n_anomaly_high_weekly: int = 0
    n_anomaly_low_weekly: int = 0


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------
class AnalyticsQuery(BaseModel):
    aoi_id: str
    start: date | None = None
    end: date | None = None


class SummaryResponse(BaseModel):
    aoi_id: str
    start: date
    end: date
    total_detections_harmonized: float
    total_detections_harmonized_lo: float | None = None
    total_detections_harmonized_hi: float | None = None
    total_frp_harmonized_mw: float
    detections_modis: int
    detections_viirs: int
    # Phase 2 layers (reference platform, from 2012): shown separately and
    # included in the headline only when include_layers is requested.
    n_static_total: int = 0
    n_night_total: int = 0
    static_share: float | None = None
    # ONE static-share metric, stated openly (v1.0.6 / Phase 3A defect 10):
    # measured on the VIIRS era only (2012-01-20 onward) - the static layer
    # does not exist before that date, so including pre-2012 comparable
    # counts in the denominator mixed modelled with unmeasurable. Numerator/
    # denominator and the definition string are exposed so the UI can show
    # them in a tooltip instead of a bare percentage.
    static_share_numerator: int | None = None
    static_share_denominator: int | None = None
    static_share_definition: str | None = None
    static_share_period_start: date | None = None
    static_share_period_end: date | None = None
    # Phase 3A (defect 5): per-era season-length medians + the definition,
    # so the UI can label the 2012 seam instead of printing one
    # implausible number.
    season_median_transferred_era: float | None = None
    season_median_viirs_era: float | None = None
    season_length_definition: str | None = None
    frp_modis_mw: float
    frp_viirs_mw: float
    active_days: int
    peak_day: CalendarCell | None
    n_anomaly_high: int
    n_anomaly_low: int
    season_current: SeasonOut | None
    season_baseline_mean_length: float | None
    top_episode: "EpisodeOut | None"
    provenance_split: dict[str, int]


class HeadlineResponse(BaseModel):
    """Headline analytic (challenge requirement): current month TO DATE vs
    the historical median of the SAME day-of-month window, comparable
    subset (v1.0.6 - previously a 1-2 day partial month was compared
    against a full-month median)."""
    aoi_id: str
    month: date
    current_total: float
    current_partial: bool
    # Day of month the record reaches (month-to-date window length).
    days_in_month: int = 0
    # Historical years whose record fully covers days 1..days_in_month of
    # this calendar month; the median/IQR/rank are computed over THOSE
    # same-length windows, never against full-month totals.
    historical_median: float | None
    historical_p25: float | None
    historical_p75: float | None
    historical_years: list[int]
    percentile_rank: float | None
    verdict: str
    note: str = ""
    # Minimum days into the month before a verdict is shown at all.
    min_days_for_verdict: int = 5
    comparison: str = "month-to-date vs same day-of-month window in history"
    # Phase 3A (defect 6): the PRIMARY figure is the last COMPLETE month
    # (a real month vs the historical distribution of that calendar month).
    # The in-progress month-to-date comparison above stays as secondary.
    complete_month: date | None = None
    complete_total: float | None = None
    complete_median: float | None = None
    complete_p25: float | None = None
    complete_p75: float | None = None
    complete_years: list[int] = []
    complete_percentile_rank: float | None = None
    complete_verdict: str | None = None
    complete_note: str = ""
    # STEP B (pending fixes): "provisional" NRT labelling + Oct/NRT
    # diagnostics. A month whose rows include NRT product data is still
    # revisable upstream, so the UI must label it provisional and say how
    # much of the month is NRT.
    provisional: bool = False
    nrt_share_month: float | None = None
    nrt_share_complete: float | None = None
    provisional_note: str = ""


class TimeseriesPoint(BaseModel):
    date: date
    n_modis: int
    n_viirs: int
    n_harmonized: float
    n_harmonized_lo: float | None = None
    n_harmonized_hi: float | None = None
    n_static: int = 0
    n_night: int = 0
    frp_modis_mw: float
    frp_viirs_mw: float
    frp_harmonized_mw: float
    z_n: float | None
    # Which LOYO interval the band uses at THIS aggregation (v1.0.6):
    # "annual-LOYO" (day), "weekly-LOYO" (week), "monthly-LOYO" (month),
    # or None in the native era / no-transfer databases.
    interval_basis: str | None = None


class ClimatologyDoy(BaseModel):
    doy: int
    mean_n: float
    p10_n: float
    p90_n: float
    mean_frp: float


class ClimatologyResponse(BaseModel):
    aoi_id: str
    baseline: BaselineInfo
    doy: list[ClimatologyDoy]


class EpisodeOut(BaseModel):
    id: int
    aoi_id: str
    label: str
    start_date: date
    end_date: date
    duration_days: int
    n_detections: int
    peak_frp_mw: float
    total_frp_mw: float
    intensity_index: float
    centroid_lat: float
    centroid_lon: float
    bounds: list[float]
    sensor_mix: dict[str, int]
    is_critical: bool


class CalibrationOut(BaseModel):
    variable: str
    direction: str
    method: str
    slope: float
    intercept: float
    r2_in_sample: float   # log space
    r2_oos: float         # log space, chronological holdout
    r2_raw_oos: float     # raw scale, chronological holdout
    n_months: int
    n_pairs: int
    window_start: date
    window_end: date
    agreement_stat: float  # min Spearman rho vs each sensor, overlap era
    params: dict


class MethodsResponse(BaseModel):
    aoi_id: str
    calibrations: list[CalibrationOut]
    pairing_stats: dict
    pipeline: dict
    # Code limitations (source of truth, ships with the app) + the snapshot
    # taken when the pipeline last rebuilt this AOI (history).
    limitations: list[str] = []
    limitations_at_rebuild: list[str] | None = None
    citations: list[str]


# --------------------------------------------------------------------------
# Live fetch
# --------------------------------------------------------------------------
class LiveFetchRequest(BaseModel):
    aoi_id: str
    day_range: int = Field(default=1, ge=1, le=5)
    day: date | None = Field(default=None, description="Optional FIRMS day; defaults to most recent available.")


class LiveFetchResponse(BaseModel):
    status: Literal["OK", "PARTIAL", "FAILED", "UNREACHABLE", "SKIPPED"]
    mode: str
    message: str
    inserted: int
    deduped: int
    rejected: int
    per_source: list["SourceResult"]


class SourceResult(BaseModel):
    source: str
    status: str
    rows: int
    message: str = ""


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------
class PlaybackFrame(BaseModel):
    date: date
    n_modis: int
    n_viirs: int
    frp_harmonized_mw: float
    points: list[list[float]]  # [lat, lon, frp_mw, sensor_code(0=MODIS,1=VIIRS), conf_code]


class PlaybackResponse(BaseModel):
    aoi_id: str
    start: date
    end: date
    frames: list[PlaybackFrame]
    truncated: bool


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
ExportLevel = Literal["hotspots", "daily", "harmonized", "episodes"]
ExportFormat = Literal["csv", "geojson"]


# Resolve forward references (EpisodeOut / CalendarCell defined after their
# referencing models for readability).
HealthResponse.model_rebuild()
MetaResponse.model_rebuild()
HotspotCollection.model_rebuild()
CalendarResponse.model_rebuild()
SummaryResponse.model_rebuild()
LiveFetchResponse.model_rebuild()
