"""Live FIRMS ingest shared by the seeder and the /api/live/fetch route.

Fetch failures are reported explicitly; they never trigger synthetic fallback
inside this module. Successful source responses replace only their own NRT
platform/date window, making retries idempotent without touching FIRMS archive
rows or other live platforms.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..harmonize.pipeline import run_pipeline_for_aoi
from ..ingest.dedup import deduplicate
from ..ingest.firms_client import SOURCES_MODIS, SOURCES_VIIRS, fetch_area_csv
from ..ingest.parser import parse_firms_csv
from ..ingest.persist import create_run, finish_run, replace_firms_nrt_window
from ..models import Aoi

# The NRT platform contract intentionally excludes S-NPP: NASA says new data
# delivery ceased at 13:00 UTC on 2026-11-02; historical products remain
# available. Never reinterpret converted MODIS records as VIIRS observations;
# every accepted record must match its native FIRMS feed.
SOURCE_CONTRACTS = {
    "MODIS_NRT": ("MODIS", {"Terra", "Aqua"}),
    "VIIRS_NOAA20_NRT": ("VIIRS", {"N20"}),
    "VIIRS_NOAA21_NRT": ("VIIRS", {"N21"}),
}
NRT_PROVENANCE = {
    "product": "NRT",
    "near_real_time": True,
    "science_quality": "provisional_unreviewed",
    "uncertainty": (
        "NRT detections are provisional and may be revised; they are native "
        "FIRMS observations, not MODIS-to-VIIRS-equivalent estimates."
    ),
    "platform_policy": (
        "MODIS Terra/Aqua and VIIRS NOAA-20/NOAA-21 only; new S-NPP data "
        "delivery ceased at 13:00 UTC on 2026-11-02. Historical products remain available."
    ),
}


def ingest_firms_for_aoi(
    db: Session, aoi: Aoi, day_range: int = 1, day: date | None = None
) -> dict:
    """Fetch FIRMS NRT CSVs, validate native platforms, replace fetched windows."""
    cfg = get_settings()
    run = create_run(db, mode="LIVE", source="firms_api", aoi_id=aoi.id)
    per_source: list[dict] = []
    rows_in = rows_deduped = rows_rejected = 0
    inserted = synthetic_removed = 0
    source_errors: list[str] = []
    successful_sources = 0
    sources = [(source, "MODIS") for source in SOURCES_MODIS] + [
        (source, "VIIRS") for source in SOURCES_VIIRS
    ]

    if not cfg.firms_map_key:
        for source, _family in sources:
            per_source.append({
                "source": source,
                "status": "NOT_CONFIGURED",
                "rows": 0,
                "message": "FIRMS_MAP_KEY not set",
            })
        message = "LIVE ingest failed: FIRMS_MAP_KEY is required; no seed fallback was used."
        finish_run(
            db, run, "FAILED", message, 0, 0, 0, 0,
            meta={"provenance": NRT_PROVENANCE, "sources": per_source},
        )
        return {
            "status": "FAILED", "mode": "LIVE", "message": message,
            "inserted": 0, "deduped": 0, "rejected": 0,
            "per_source": per_source, "pipeline": {},
        }

    effective_range = max(1, day_range)
    if day is None:
        # With DATE omitted, FIRMS returns the most recent dates, ending today.
        window_end = date.today()
        window_start = window_end - timedelta(days=effective_range - 1)
    else:
        # The documented DATE form returns DATE through DATE + DAY_RANGE - 1.
        window_start = day
        window_end = day + timedelta(days=effective_range - 1)
    synthetic_cleanup_pending = True
    any_window_replaced = False

    for source, family in sources:
        expected_family, expected_satellites = SOURCE_CONTRACTS[source]
        result = fetch_area_csv(source, aoi.bbox, day_range, day, cfg)
        entry = {
            "source": source,
            "status": result.status,
            "rows": 0,
            "message": result.error,
            "product": "NRT",
            "native_platforms": sorted(expected_satellites),
        }
        per_source.append(entry)
        if result.status != "OK":
            detail = f"{source}: {result.status} {result.error}".strip()
            source_errors.append(detail)
            continue

        records, rejected, total, samples = parse_firms_csv(
            result.text, aoi.id, family, product="NRT"
        )
        rows_in += total
        rows_rejected += rejected
        if rejected or (not records and samples):
            entry["status"] = "PARSE_ERROR"
            entry["message"] = "; ".join(samples) or f"{rejected} malformed rows"
            source_errors.append(f"{source}: invalid FIRMS CSV ({entry['message']})")
            continue

        invalid_platform = [
            record for record in records
            if record.sensor_family != expected_family
            or record.satellite not in expected_satellites
        ]
        invalid_date = [
            record for record in records
            if record.acq_date < window_start or record.acq_date > window_end
        ]
        if invalid_platform:
            rows_rejected += len(invalid_platform)
            entry["status"] = "PLATFORM_MISMATCH"
            entry["message"] = (
                "Feed returned records outside its native platform contract: "
                + ", ".join(sorted({r.satellite for r in invalid_platform}))
            )
            source_errors.append(f"{source}: {entry['message']}")
            continue
        if invalid_date:
            rows_rejected += len(invalid_date)
            entry["status"] = "DATE_RANGE_MISMATCH"
            entry["message"] = (
                f"Feed returned acquisition dates outside {window_start.isoformat()}"
                f"..{window_end.isoformat()}"
            )
            source_errors.append(f"{source}: {entry['message']}")
            continue

        kept, deduped = deduplicate(
            records,
            cfg.dedup_max_gap_minutes,
            cfg.dedup_max_distance_km_modis,
            cfg.dedup_max_distance_km_viirs,
        )
        rows_deduped += deduped
        entry["rows"] = len(kept)
        inserted_now, removed_now = replace_firms_nrt_window(
            db,
            aoi.id,
            run.id,
            kept,
            expected_satellites,
            window_start,
            window_end,
            clear_synthetic=bool(kept) and synthetic_cleanup_pending,
        )
        inserted += inserted_now
        synthetic_removed += removed_now
        if kept:
            synthetic_cleanup_pending = False
        successful_sources += 1
        any_window_replaced = True

    if successful_sources == 0:
        status = "FAILED"
        message = "LIVE FIRMS ingest failed; no source window was refreshed: " + (
            "; ".join(source_errors) if source_errors else "no usable FIRMS sources"
        )
    elif source_errors:
        status = "PARTIAL"
        message = "Partial live FIRMS ingest: " + "; ".join(source_errors)
    else:
        status = "OK"
        message = "Live FIRMS NRT ingest complete."

    finish_run(
        db,
        run,
        status,
        message,
        rows_in,
        inserted,
        rows_deduped,
        rows_rejected,
        meta={
            "provenance": NRT_PROVENANCE,
            "sources": per_source,
            "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
            "synthetic_rows_removed": synthetic_removed,
        },
    )
    pipeline_summary: dict = {}
    if any_window_replaced:
        pipeline_summary = run_pipeline_for_aoi(db, aoi)
    return {
        "status": status,
        "mode": "LIVE",
        "message": message,
        "inserted": inserted,
        "deduped": rows_deduped,
        "rejected": rows_rejected,
        "per_source": per_source,
        "pipeline": pipeline_summary,
    }


def latest_available_day() -> date:
    """FIRMS NRT data is published with a few hours latency; default to yesterday."""
    return date.today() - timedelta(days=1)
