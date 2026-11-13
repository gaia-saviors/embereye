"""EmberEye backend configuration.

Twelve-factor settings via environment variables. Every value has an
operational default so `docker compose up --build` works with zero setup.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "EmberEye"
    app_version: str = "1.0.9"
    seed_version: str = "2026.10.1"
    log_level: str = "INFO"
    cors_origins: str = "*"

    # --- persistence -------------------------------------------------------
    # Compose injects EMBEREBEYE_DATABASE_URL (Postgres). Local dev/tests
    # fall back to SQLite. The explicit alias avoids colliding with
    # unrelated DATABASE_URL variables from other tools on the host.
    database_url: str = Field(
        default="sqlite:///./embereye_dev.db",
        validation_alias=AliasChoices("EMBEREBEYE_DATABASE_URL", "EMBEREBEYE_DB_URL"),
    )
    sql_echo: bool = False
    # SQLite journal mode. WAL is fastest for the single-writer dev case, but
    # a database file mounted from a Windows host into a container (the
    # docker-compose.sqlite.yml path) can require "DELETE" - overridable
    # without touching code.
    sqlite_journal_mode: str = "WAL"

    # --- NASA FIRMS access ---------------------------------------------------
    firms_map_key: str = ""
    firms_base_url: str = "https://firms.modaps.eosdis.nasa.gov"
    firms_timeout_seconds: float = 30.0
    firms_max_retries: int = 2

    # --- data acquisition / pipeline ----------------------------------------
    # AUTO: use live FIRMS when a MAP_KEY is configured and reachable,
    #       otherwise ingest the deterministic calibrated seed archive.
    # SEED: force seed archive. LIVE: require live FIRMS (error if unreachable).
    data_mode: str = "AUTO"
    seed_start: date = date(2000, 11, 2)
    seed_end: date = date(2026, 11, 1)
    seed_scale: float = 1.0
    baseline_start: int = 2012
    baseline_end: int = 2019
    anomaly_z_threshold: float = 2.0
    anomaly_min_baseline_years: int = 5
    # Phase 2 calibration window: MODIS overpass is stable until 2022
    # (drift starts 2023: Terra 8.8->7.1 UTC, Aqua 11.2->13.1 UTC), so the
    # month-of-year transfer is calibrated on 2012-2022 only.
    calibration_start: int = 2012
    calibration_end: int = 2022
    # S-NPP end of delivery; from this date the series continues on NOAA-20
    # divided by the overlap-era continuity factors.
    snpp_end: date = date(2026, 11, 2)
    # Month-of-year transfer guards: months whose pooled MODIS calibration
    # total is below `moy_min_month_total` fall back to the annual ratio;
    # the per-platform variant only wins when its LOYO MAPE is lower by
    # more than `moy_per_platform_margin`.
    moy_min_month_total: float = 100.0
    moy_per_platform_margin: float = 0.01
    # Retired Phase 1 seam concept (kept for schema/config compatibility;
    # no longer used by the harmonization engine).
    seam_start: date = date(2012, 6, 1)
    seam_end: date = date(2014, 12, 31)

    # --- harmonization constants (literature-anchored) -----------------------
    # Zheng et al. 2026 (Remote Sensing 18(10):1650) duplicate rule adapted to
    # FIRMS CSV granularity (minute-resolution acquisition times). Distance
    # thresholds are per sensor: a true duplicate is a sub-pixel repeat, so
    # VIIRS 375 m uses 0.2 km and MODIS 1 km uses 0.8 km.
    dedup_max_gap_minutes: int = 1
    dedup_max_distance_km_modis: float = 0.8
    dedup_max_distance_km_viirs: float = 0.2
    # Near-synchronous Aqua MODIS <-> S-NPP VIIRS pairing window.
    pair_max_delta_minutes: int = 5
    # MODIS footprint model: detections within this radius of a MODIS pixel
    # centre are considered inside its footprint.
    footprint_containment_km: float = 1.0
    # Episode clustering (Su & Yu 2026 style spatio-temporal chaining).
    episode_space_km: float = 5.0
    episode_time_days: int = 2
    # Static-source mask: a location detected on > this fraction of days over
    # the record is flagged (gas flares, industrial heat sources).
    static_source_min_days: int = 60
    static_source_cell_deg: float = 0.05

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*" or raw == "":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
