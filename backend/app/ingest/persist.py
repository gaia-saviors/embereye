"""Ingest persistence: canonical records -> Hotspot rows.

Bulk, deterministic, idempotent per run. Static-source masking is applied
in up to three passes: (1) the FIRMS `type` column at parse time (SP rows,
type=2), (2) the SP-learned static location list for NRT rows at ingest
time, and (3) the persistence/T4-stability mask once the full record
history for an AOI is present (a location detected on >50% of days with
detections is a gas flare / industrial heat source, not a fire event).
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..ingest.canonical import CanonicalRecord
from ..models import Hotspot, IngestRun


def create_run(db: Session, mode: str, source: str, aoi_id: str | None) -> IngestRun:
    run = IngestRun(mode=mode, source=source, aoi_id=aoi_id, status="RUNNING")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_run(db: Session, run: IngestRun, status: str, message: str,
               rows_in: int, rows_kept: int, rows_deduped: int, rows_rejected: int,
               meta: dict | None = None) -> IngestRun:
    run.status = status
    run.message = message
    run.rows_in = rows_in
    run.rows_kept = rows_kept
    run.rows_deduped = rows_deduped
    run.rows_rejected = rows_rejected
    run.meta_json = json.dumps(meta or {}, sort_keys=True)
    from ..models import utcnow
    run.finished_at = utcnow()
    db.commit()
    db.refresh(run)
    return run


def _bulk_insert(db: Session, records: list[CanonicalRecord], chunk: int = 20000) -> None:
    """Insert using raw VALUES batches - portable and fast on both dialects."""
    from sqlalchemy import insert
    for start in range(0, len(records), chunk):
        part = records[start:start + chunk]
        db.execute(
            insert(Hotspot),
            [
                {
                    "aoi_id": r.aoi_id,
                    "sensor_family": r.sensor_family,
                    "satellite": r.satellite,
                    "instrument": r.instrument,
                    "resolution_m": r.resolution_m,
                    "acq_dt_utc": r.acq_dt_utc,
                    "acq_date": r.acq_date,
                    "local_date": r.local_date,
                    "lat": r.lat,
                    "lon": r.lon,
                    "brightness_t4_k": r.brightness_t4_k,
                    "brightness_t11_k": r.brightness_t11_k,
                    "frp_mw": r.frp_mw,
                    "confidence_class": r.confidence_class,
                    "confidence_raw": r.confidence_raw,
                    "daynight": r.daynight,
                    "scan_km": r.scan_km,
                    "track_km": r.track_km,
                    "pixel_area_km2": r.pixel_area_km2,
                    "saturated": r.saturated,
                    "band21_fallback": r.band21_fallback,
                    "static_source": r.static_source,
                    "source_version": r.source_version,
                    "firms_product": r.firms_product,
                    "firms_type": r.firms_type,
                    "run_id": r.run_id,
                }
                for r in part
            ],
        )
    db.commit()


def persist_records(db: Session, run_id: int, records: list[CanonicalRecord]) -> int:
    for r in records:
        r.run_id = run_id
    _bulk_insert(db, records)
    return len(records)


def replace_firms_nrt_window(
    db: Session,
    aoi_id: str,
    run_id: int,
    records: list[CanonicalRecord],
    satellites: set[str],
    start: date,
    end: date,
    clear_synthetic: bool = False,
) -> tuple[int, int]:
    """Replace one successfully fetched NRT platform/date window.

    FIRMS rows outside this exact AOI, platform and acquisition-date window
    (including SP archive rows) are retained. A successful empty CSV therefore
    removes stale rows for only the requested window. Optionally remove the
    AOI's synthetic archive in the same transaction when it transitions to
    real FIRMS data, so synthetic and observed detections are never blended.
    Returns (rows inserted, synthetic rows removed).
    """
    synthetic_removed = 0
    if clear_synthetic:
        result = db.execute(
            delete(Hotspot).where(
                Hotspot.aoi_id == aoi_id,
                Hotspot.firms_product == "",
            )
        )
        synthetic_removed = int(result.rowcount or 0)
    if satellites:
        db.execute(
            delete(Hotspot).where(
                Hotspot.aoi_id == aoi_id,
                Hotspot.firms_product == "NRT",
                Hotspot.satellite.in_(sorted(satellites)),
                Hotspot.acq_date >= start,
                Hotspot.acq_date <= end,
            )
        )
    for record in records:
        record.run_id = run_id
    if records:
        _bulk_insert(db, records)
    else:
        db.commit()
    return len(records), synthetic_removed


def clear_aoi_hotspots(db: Session, aoi_id: str, before: date | None = None) -> None:
    stmt = delete(Hotspot).where(Hotspot.aoi_id == aoi_id)
    if before is not None:
        stmt = stmt.where(Hotspot.local_date < before)
    db.execute(stmt)
    db.commit()


def replace_aoi_records(db: Session, aoi_id: str, records: list[CanonicalRecord],
                        run_id: int) -> int:
    """Idempotent ingest: wipe the AOI's rows, insert the new canonical set."""
    clear_aoi_hotspots(db, aoi_id)
    return persist_records(db, run_id, records)


def apply_static_source_mask(db: Session, aoi_id: str, min_days: int,
                             cell_deg: float) -> int:
    """Phase 2 secondary tag: the persistence mask marks "recurring source".

    Classification policy (Phase 2, item 4):
      * PRIMARY static definition (SP era): FIRMS `type=2`, applied at parse
        time; NRT rows inherit it via the SP-learned static location list.
      * SECONDARY tag: this 60-day / T4-stability persistence mask now sets
        `recurring_source` (new column) and no longer overwrites
        `static_source`. A location detected on >= `min_days` distinct days
        with a hot (> 320 K), near-zero-variance daytime T4 series is a
        recurring source (gas flare, industrial heat source, or - the open
        question the audit cross-tab settles - recurring crop-residue
        burning). Flag only, never a deletion.
    """
    rows = db.execute(
        select(
            Hotspot.id, Hotspot.lat, Hotspot.lon, Hotspot.local_date,
            Hotspot.daynight, Hotspot.brightness_t4_k, Hotspot.sensor_family,
        ).where(Hotspot.aoi_id == aoi_id)
    ).all()
    if not rows:
        return 0

    # Evidence is evaluated per (cell, sensor): mixing sensors into one T4
    # series would destroy the stability test (different calibrations).
    days_per_cell: dict[tuple, set[date]] = {}
    t4_by_cell_sensor: dict[tuple, list[float]] = {}
    ids_by_cell: dict[tuple, list[int]] = {}
    for rid, lat, lon, local_date, daynight, t4, sensor in rows:
        cell = (int(lat / cell_deg), int(lon / cell_deg))
        days_per_cell.setdefault(cell, set()).add(local_date)
        if daynight == "D" and t4 is not None:
            t4_by_cell_sensor.setdefault((cell, sensor), []).append(float(t4))
        ids_by_cell.setdefault(cell, []).append(rid)

    flagged_ids: list[int] = []
    for cell, days in days_per_cell.items():
        if len(days) < min_days:
            continue  # too little evidence to judge persistence
        recurring = False
        for sensor in ("MODIS", "VIIRS"):
            t4s = t4_by_cell_sensor.get((cell, sensor), [])
            if len(t4s) < min_days / 2:
                continue
            mean = sum(t4s) / len(t4s)
            var = sum((t - mean) ** 2 for t in t4s) / len(t4s)
            # Persistent cells: hot on many independent days, thermally
            # stable; tagged as recurring sources (secondary evidence).
            if var < 25.0 and mean > 320.0:
                recurring = True
        if recurring:
            flagged_ids.extend(ids_by_cell[cell])

    if flagged_ids:
        for start in range(0, len(flagged_ids), 10000):
            db.execute(
                update(Hotspot)
                .where(Hotspot.id.in_(flagged_ids[start:start + 10000]))
                .values(recurring_source=True)
            )
        db.commit()
    return len(flagged_ids)


def _span_days(days: set[date]) -> int:
    lo, hi = min(days), max(days)
    return max(1, (hi - lo).days)
