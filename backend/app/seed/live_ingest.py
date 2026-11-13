"""Live FIRMS ingest shared by the seeder and the /api/live/fetch route.

Failure contract: any network/parse failure is reported in the response,
never raised past this module. Partial success (one source up, another
down) is a valid PARTIAL outcome.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..harmonize.pipeline import run_pipeline_for_aoi
from ..ingest.canonical import CanonicalRecord
from ..ingest.dedup import deduplicate
from ..ingest.firms_client import SOURCES_MODIS, SOURCES_VIIRS, fetch_area_csv
from ..ingest.parser import parse_firms_csv
from ..ingest.persist import create_run, finish_run, persist_records
from ..models import Aoi


def ingest_firms_for_aoi(
    db: Session, aoi: Aoi, day_range: int = 1, day: date | None = None
) -> dict:
    """Fetch FIRMS CSVs for the AOI bbox, normalize, dedup, persist, re-pipeline."""
    cfg = get_settings()
    run = create_run(db, mode="LIVE", source="firms_api", aoi_id=aoi.id)
    per_source: list[dict] = []
    all_records: list[CanonicalRecord] = []
    rows_in = rows_deduped = rows_rejected = 0
    any_ok = any_attempted = False
    errors: list[str] = []

    sources = [(s, "MODIS") for s in SOURCES_MODIS] + [(s, "VIIRS") for s in SOURCES_VIIRS]
    for source, family in sources:
        result = fetch_area_csv(source, aoi.bbox, day_range, day, cfg)
        entry = {"source": source, "status": result.status, "rows": 0, "message": result.error}
        per_source.append(entry)
        if result.status in {"NOT_CONFIGURED", "UNREACHABLE", "HTTP_ERROR", "EMPTY"}:
            errors.append(f"{source}: {result.status} {result.error}".strip())
            continue
        any_attempted = True
        records, rejected, total, _samples = parse_firms_csv(
            result.text, aoi.id, family, product="NRT")
        rows_in += total
        rows_rejected += rejected
        kept, deduped = deduplicate(
            records, cfg.dedup_max_gap_minutes,
            cfg.dedup_max_distance_km_modis, cfg.dedup_max_distance_km_viirs,
        )
        rows_deduped += deduped
        entry["rows"] = len(kept)
        if kept:
            any_ok = True
            persist_records(db, run.id, kept)
            all_records.extend(kept)

    if not cfg.firms_map_key:
        status = "SKIPPED"
        message = "FIRMS MAP_KEY not configured; platform remains on the seed archive."
    elif not any_attempted:
        status = "UNREACHABLE"
        message = "FIRMS unreachable: " + ("; ".join(errors) if errors else "network error")
    elif any_ok and len(errors) < len(sources):
        status = "OK" if not errors else "PARTIAL"
        message = "Live FIRMS ingest complete." if not errors else f"Partial: {'; '.join(errors)}"
    elif any_ok:
        status = "PARTIAL"
        message = "Ingested with source errors: " + "; ".join(errors)
    else:
        status = "FAILED"
        message = "All FIRMS sources failed: " + "; ".join(errors)

    finish_run(db, run, status, message, rows_in, len(all_records),
               rows_deduped, rows_rejected, meta={"sources": per_source})

    pipeline_summary: dict = {}
    if any_ok:
        pipeline_summary = run_pipeline_for_aoi(db, aoi)

    return {
        "status": status,
        "mode": "LIVE",
        "message": message,
        "inserted": len(all_records),
        "deduped": rows_deduped,
        "rejected": rows_rejected,
        "per_source": per_source,
        "pipeline": pipeline_summary,
    }


def latest_available_day() -> date:
    """FIRMS NRT data is published with a few hours latency; default to
    yesterday (UTC) to guarantee coverage."""
    return date.today() - timedelta(days=1)
