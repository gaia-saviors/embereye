/** EmberEye API types - mirror of backend/app/schemas.py (frozen contract). */

export type SensorFamily = "MODIS" | "VIIRS";
export type ConfidenceClass = "LOW" | "NOMINAL" | "HIGH";
export type Provenance =
  | "MODIS_TRANSFERRED"
  | "VIIRS_NATIVE"
  | "NO_DATA"
  // legacy values that can still sit in pre-Phase-2 databases
  | "MODIS_ONLY"
  | "OVERLAP_FUSED"
  | "VIIRS_FORWARD";
export type DataMode = "AUTO" | "SEED" | "LIVE";

export interface FirmsStatus {
  key_configured: boolean;
  reachable: boolean | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
}

export interface StoreCounts {
  aois: number;
  hotspots: number;
  harmonized_days: number;
  episodes: number;
  coverage_start: string | null;
  coverage_end: string | null;
}

export interface IngestSummary {
  id: number;
  mode: string;
  source: string;
  status: string;
  rows_kept: number;
  rows_deduped: number;
  rows_rejected: number;
  started_at: string;
  finished_at: string | null;
  message: string;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  db: "ok" | "error";
  data_mode: DataMode;
  effective_data_mode: "LIVE" | "SEED";
  seed_version: string;
  firms: FirmsStatus;
  counts: StoreCounts;
  last_ingest: IngestSummary | null;
}

export interface SensorInfo {
  family: SensorFamily;
  satellites: string[];
  resolution_m: number;
  record_start: string;
  swath_km: number;
  overpass_lst: string[];
  confidence_scheme: string;
}

export interface EraInfo {
  name: string;
  start: string;
  end: string;
  description: string;
}

export interface BaselineInfo {
  start_year: number;
  end_year: number;
  kernel_days: number;
  z_threshold: number;
  definition: string;
}

export interface MethodologySummary {
  harmonization_tier: string;
  steps: string[];
  units: string;
}

export interface FirmsSeam {
  satellite: string;
  sp_last_date: string | null;
  nrt_first_date: string | null;
  provisional: boolean;
  note: string;
}

/** Data-source badge substrate - DERIVED FROM THE DATA (v1.0.6, P2b).
 * The word LIVE is never shown unless auto_refresh is true. */
export interface DataSourceInfo {
  kind: "FIRMS_ARCHIVE" | "SYNTHETIC_SEED" | "MIXED" | "EMPTY";
  label: string;
  auto_refresh: boolean;
  n_hotspots: number;
  products: Record<string, number>;
  first_acquisition: string | null;
  last_acquisition: string | null;
  last_ingest_at: string | null;
  last_ingest_status: string | null;
  detail: string;
}

export interface MetaResponse {
  name: string;
  tagline: string;
  version: string;
  public_read_only: boolean;
  seed_version: string;
  data_mode: DataMode;
  effective_data_mode: "LIVE" | "SEED";
  data_source: DataSourceInfo;
  seed_disclaimer: string | null;
  sensors: SensorInfo[];
  eras: EraInfo[];
  firms_seams: FirmsSeam[];
  baseline: BaselineInfo;
  methodology: MethodologySummary;
  citations: string[];
}

export interface Aoi {
  id: string;
  name: string;
  description: string;
  geojson: { type: "Polygon"; coordinates: number[][][] };
  west: number;
  south: number;
  east: number;
  north: number;
  is_preset: boolean;
  created_at: string;
  // Phase 3A (defect 7): false = no data loaded for this AOI in the current
  // loaded database; the UI disables it ("no data loaded in this version").
  has_data: boolean;
  job_id?: string | null;
}

export interface JobOut {
  id: string;
  aoi_id: string;
  status: "PENDING" | "RUNNING" | "DONE" | "FAILED";
  progress: number;
  message: string;
  started_at: string;
  finished_at: string | null;
}

export interface HotspotProps {
  id: number;
  sensor: SensorFamily;
  satellite: string;
  acq_dt_utc: string;
  local_date: string;
  frp_mw: number | null;
  t4_k: number | null;
  t11_k: number | null;
  confidence: ConfidenceClass;
  daynight: "D" | "N";
  pixel_area_km2: number;
  saturated: boolean;
  band21_fallback: boolean;
  static_source: boolean;
  firms_product: "SP" | "NRT" | null;
  firms_type: number | null;
}

export interface HotspotFeature {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: HotspotProps;
}

export interface HotspotCollection {
  type: "FeatureCollection";
  features: HotspotFeature[];
  meta: { total: number; returned: number; offset: number; truncated: boolean };
}

export interface CalendarCell {
  date: string;
  year: number;
  doy: number;
  n_modis: number;
  n_viirs: number;
  n_harmonized: number;
  n_harmonized_lo?: number | null;
  n_harmonized_hi?: number | null;
  n_comparable_modis?: number;
  n_comparable_viirs?: number;
  n_static?: number;
  n_night?: number;
  static_share?: number | null;
  frp_modis_mw: number;
  frp_viirs_mw: number;
  frp_harmonized_mw: number;
  z_n?: number | null;
  z_frp?: number | null;
  pct_n?: number | null;
  flag_high?: boolean;
  flag_low?: boolean;
  seam_weight?: number;
  provenance: Provenance;
}

export interface SeasonOut {
  year: number;
  onset_date: string | null;
  end_date: string | null;
  season_length_days: number | null;
  peak_date: string | null;
  peak_value: number;
  active_days: number;
  max_streak_days: number;
  is_partial: boolean;
}

/** One ISO-week cell of the transferred (MODIS) era - Phase 3A defect 3.
 * Anomalies for this era are computed at WEEK level against other
 * transferred-era years only. Drawn with an estimated marker. */
export interface TransferredWeek {
  year: number;
  iso_week: number;
  start_date: string;
  end_date: string;
  n_harmonized: number;
  n_harmonized_lo: number | null;
  n_harmonized_hi: number | null;
  pct_n: number | null;
  flag_high: boolean;
  flag_low: boolean;
  n_days_with_data: number;
}

export interface CalendarResponse {
  aoi_id: string;
  baseline: BaselineInfo;
  cells: CalendarCell[];
  seasons: SeasonOut[];
  summary: {
    max_n: number;
    max_frp: number;
    n_anomaly_high: number;
    n_anomaly_low: number;
    n_anomaly_high_native: number;
    n_anomaly_low_native: number;
    n_anomaly_high_weekly: number;
    n_anomaly_low_weekly: number;
    years: number[];
  };
  // Phase 3A: first VIIRS-native day (the seam). Years/days before it are
  // the transferred era - drawn weekly, marked estimated.
  viirs_start_date: string | null;
  transferred_weeks: TransferredWeek[];
}

export interface SummaryResponse {
  aoi_id: string;
  start: string;
  end: string;
  total_detections_harmonized: number;
  total_detections_harmonized_lo: number | null;
  total_detections_harmonized_hi: number | null;
  total_frp_harmonized_mw: number;
  detections_modis: number;
  detections_viirs: number;
  n_static_total: number;
  n_night_total: number;
  static_share: number | null;
  // ONE static-share metric with its arithmetic exposed (v1.0.6, P2c /
  // Phase 3A defect 10: measured on the VIIRS era only, 2012-01-20+).
  static_share_numerator: number | null;
  static_share_denominator: number | null;
  static_share_definition: string | null;
  static_share_period_start: string | null;
  static_share_period_end: string | null;
  // Phase 3A (defect 5): per-era season-length medians + definition text.
  season_median_transferred_era: number | null;
  season_median_viirs_era: number | null;
  season_length_definition: string | null;
  frp_modis_mw: number;
  frp_viirs_mw: number;
  active_days: number;
  peak_day: CalendarCell | null;
  n_anomaly_high: number;
  n_anomaly_low: number;
  season_current: SeasonOut | null;
  season_baseline_mean_length: number | null;
  top_episode: EpisodeOut | null;
  provenance_split: Record<string, number>;
}

export interface HeadlineResponse {
  aoi_id: string;
  month: string;
  current_total: number;
  current_partial: boolean;
  // Month-to-date window length (v1.0.6, P2d): history is compared over the
  // SAME day-of-month window, never the full month.
  days_in_month: number;
  historical_median: number | null;
  historical_p25: number | null;
  historical_p75: number | null;
  historical_years: number[];
  percentile_rank: number | null;
  verdict: string;
  note: string;
  min_days_for_verdict: number;
  comparison: string;
  // Phase 3A (defect 6): PRIMARY headline = the last COMPLETE month vs the
  // historical distribution of that calendar month.
  complete_month: string | null;
  complete_total: number | null;
  complete_median: number | null;
  complete_p25: number | null;
  complete_p75: number | null;
  complete_years: number[];
  complete_percentile_rank: number | null;
  complete_verdict: string | null;
  complete_note: string;
  provisional?: boolean;
  nrt_share_complete?: number | null;
  provisional_note?: string | null;
}

export interface TimeseriesPoint {
  date: string;
  n_modis: number;
  n_viirs: number;
  n_harmonized: number;
  n_harmonized_lo: number | null;
  n_harmonized_hi: number | null;
  n_static: number;
  n_night: number;
  frp_modis_mw: number;
  frp_viirs_mw: number;
  frp_harmonized_mw: number;
  z_n: number | null;
  // Which LOYO interval the band uses at THIS aggregation (v1.0.6, P2a).
  interval_basis: string | null;
}

export interface ClimatologyDoy {
  doy: number;
  mean_n: number;
  p10_n: number;
  p90_n: number;
  mean_frp: number;
}

export interface ClimatologyResponse {
  aoi_id: string;
  baseline: BaselineInfo;
  doy: ClimatologyDoy[];
}

export interface EpisodeOut {
  id: number;
  aoi_id: string;
  label: string;
  start_date: string;
  end_date: string;
  duration_days: number;
  n_detections: number;
  peak_frp_mw: number;
  total_frp_mw: number;
  intensity_index: number;
  centroid_lat: number;
  centroid_lon: number;
  bounds: [number, number, number, number];
  sensor_mix: Record<string, number>;
  is_critical: boolean;
}

export interface CalibrationOut {
  variable: string;
  direction: string;
  method: string;
  slope: number;
  intercept: number;
  r2_in_sample: number;
  r2_oos: number;
  r2_raw_oos: number;
  n_months: number;
  n_pairs: number;
  window_start: string;
  window_end: string;
  agreement_stat: number;
  params: Record<string, unknown>;
}

export interface MethodsResponse {
  aoi_id: string;
  calibrations: CalibrationOut[];
  pairing_stats: Record<string, unknown>;
  pipeline: Record<string, unknown>;
  // Current limitations text (ships with the app, source of truth).
  limitations: string[];
  // Snapshot of the limitations when this AOI was last rebuilt (history).
  limitations_at_rebuild: string[] | null;
  citations: string[];
}

export interface LiveFetchResponse {
  status: "OK" | "PARTIAL" | "FAILED" | "UNREACHABLE" | "SKIPPED";
  mode: string;
  message: string;
  inserted: number;
  deduped: number;
  rejected: number;
  per_source: { source: string; status: string; rows: number; message: string }[];
}

export interface PlaybackResponse {
  aoi_id: string;
  start: string;
  end: string;
  frames: {
    date: string;
    n_modis: number;
    n_viirs: number;
    frp_harmonized_mw: number;
    points: number[][];
  }[];
  truncated: boolean;
}

// ---- v1.0.6 map data paths (P1) ------------------------------------------

/** JSON answer when an AOI is too large for a snapshot (mode="grid"). */
export interface SnapshotRedirect {
  mode: "grid";
  aoi: string;
  n_rows: number;
  max_rows: number;
  detail: string;
}

export interface GridCell {
  i: number;
  j: number;
  w: number;
  s: number;
  e: number;
  n_: number;
  bucket: string;
  n: number;
  n_modis: number;
  n_viirs: number;
  n_static: number;
  frp_mw: number;
}

export interface GridResponse {
  mode: "grid";
  aoi: string;
  res: "week" | "month";
  cell_deg: number;
  start: string | null;
  end: string | null;
  cells: GridCell[];
  meta: { total_points: number; n_cells: number; truncated: boolean; max_cells: number };
}
