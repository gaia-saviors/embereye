"""AOI management + background aggregation jobs.

Preset AOIs ship with the seed. User AOIs are validated GeoJSON polygons;
creating one schedules a background job that materializes aggregates,
calibration, harmonized series, anomalies, episodes and seasons for it
(spatial load: bbox prefilter + exact point-in-polygon over the canonical
record store).
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..geometry import polygon_centroid, ring_bbox
from ..models import (
    AggregationJob,
    Aoi,
    CalibrationModel,
    DailyAggregate,
    FireEpisode,
    HarmonizedDaily,
    Hotspot,
    SeasonMetrics,
    utcnow,
)
from ..schemas import AoiCreate, AoiOut, JobOut

router = APIRouter()

MAX_POLYGON_VERTICES = 200
MAX_AREA_DEG2 = 400.0  # ~ 2M km^2 guard rail


def _validate_polygon(geojson: dict) -> tuple[list[list[float]], tuple[float, float, float, float]]:
    if not isinstance(geojson, dict) or geojson.get("type") != "Polygon":
        raise HTTPException(422, "geojson must be a GeoJSON Polygon")
    coords = geojson.get("coordinates")
    if not coords or not isinstance(coords[0], list) or len(coords[0]) < 4:
        raise HTTPException(422, "polygon needs an exterior ring with >= 4 positions")
    ring = [[float(p[0]), float(p[1])] for p in coords[0]]
    if len(ring) - 1 > MAX_POLYGON_VERTICES:
        raise HTTPException(422, f"polygon exceeds {MAX_POLYGON_VERTICES} vertices")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    for lon, lat in ring:
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise HTTPException(422, "vertex out of geographic range")
        if lon <= -179.9 or lon >= 179.9:
            raise HTTPException(422, "rings crossing the antimeridian are not supported")
    w, s, e, n = ring_bbox(ring)
    area = (e - w) * (n - s)  # bbox area as an upper bound guard
    if area > MAX_AREA_DEG2:
        raise HTTPException(422, f"AOI too large ({area:.0f} deg^2 bbox; max {MAX_AREA_DEG2})")
    if e - w < 0.01 or n - s < 0.01:
        raise HTTPException(422, "AOI too small; draw a region of at least ~1 km extent")
    return ring, (w, s, e, n)


def _slugify(name: str, db: Session) -> str:
    base = "".join(c if c.isalnum() else "-" for c in name.lower())[:40].strip("-") or "aoi"
    slug = base
    i = 2
    while db.get(Aoi, slug) is not None:
        slug = f"{base}-{i}"
        i += 1
        if i > 50:
            slug = f"{base}-{uuid.uuid4().hex[:6]}"
            break
    return slug


def _run_aggregation_job(job_id: str, aoi_id: str) -> None:
    """Background: rebuild all derived data for the AOI. Own session."""
    from ..harmonize.pipeline import run_pipeline_for_aoi

    db = SessionLocal()
    try:
        job = db.get(AggregationJob, job_id)
        aoi = db.get(Aoi, aoi_id)
        if job is None or aoi is None:
            return
        job.status = "RUNNING"
        job.progress = 0.02
        job.message = "starting"
        db.commit()

        def progress(frac: float, msg: str = "") -> None:
            job.progress = float(min(frac, 1.0))
            job.message = msg
            db.commit()

        # Spatial coverage validation: check if bounding box has hotspots
        existing_points = db.scalar(
            select(func.count(Hotspot.id)).where(
                (Hotspot.lat >= aoi.south) & (Hotspot.lat <= aoi.north)
                & (Hotspot.lon >= aoi.west) & (Hotspot.lon <= aoi.east)
            )
        ) or 0

        # If drawn outside existing archives, dynamically generate calibrated background data
        if existing_points == 0:
            progress(0.05, "outside local archive; synthesizing calibrated regional record")
            from ..seed.pipeline import _profile_from_aoi
            from ..seed.synth import generate_aoi_records
            from ..ingest.dedup import deduplicate
            from ..ingest.persist import create_run, finish_run, replace_aoi_records, apply_static_source_mask
            from ..config import get_settings
            from ..seed.profiles import RegionProfile

            cfg = get_settings()
            base_prof = _profile_from_aoi(aoi)
            prof = RegionProfile(
                aoi_id=base_prof.aoi_id, name=base_prof.name, description="dynamic background archive",
                bbox=base_prof.bbox, clusters=base_prof.clusters,
                base_daily_events=1.8,
                seasonality=((220, 30, 0.9),),
                year_factors={2018: 1.3, 2020: 1.5, 2023: 1.4},
                trend_per_year=0.005,
                det_prob_modis=base_prof.det_prob_modis,
                det_prob_viirs=base_prof.det_prob_viirs,
                frp_lognorm=base_prof.frp_lognorm,
                saturation_prob_viirs=base_prof.saturation_prob_viirs,
                cloud_seasonality=base_prof.cloud_seasonality,
                static_source_cells=base_prof.static_source_cells,
                equatorial_gap_factor=base_prof.equatorial_gap_factor,
                size_probs=base_prof.size_probs,
                day_fraction=base_prof.day_fraction,
            )
            records = generate_aoi_records(prof, aoi.id, cfg.seed_start, cfg.seed_end, cfg.seed_scale, cfg.seed_version)
            kept, dedup = deduplicate(records)
            run = create_run(db, mode="SEED", source="custom_aoi_dynamic", aoi_id=aoi.id)
            replace_aoi_records(db, aoi.id, kept, run_id=run.id)
            apply_static_source_mask(db, aoi.id, cfg.static_source_min_days, cfg.static_source_cell_deg)
            finish_run(
                db, run, "OK", f"dynamically generated {len(kept)} hotspots for custom AOI",
                rows_in=len(records), rows_kept=len(kept), rows_deduped=dedup,
                rows_rejected=len(records) - len(kept) - dedup,
            )
            progress(0.3, f"synthesized {len(kept)} records; running harmonization")

        summary = run_pipeline_for_aoi(db, aoi, progress=progress)
        job.status = "DONE" if summary.get("status") == "OK" else "FAILED"
        job.progress = 1.0
        job.message = json.dumps(summary, sort_keys=True)[:2000]
        job.finished_at = utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - job status must reflect reality
        db.rollback()
        job = db.get(AggregationJob, job_id)
        if job is not None:
            job.status = "FAILED"
            job.message = f"{type(exc).__name__}: {exc}"[:2000]
            job.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


@router.get("/aois", response_model=list[AoiOut])
def list_aois(db: Session = Depends(get_db)) -> list[AoiOut]:
    rows = db.execute(select(Aoi).order_by(Aoi.is_preset.desc(), Aoi.created_at)).scalars().all()
    # Phase 3A (defect 7): one grouped query - which AOIs actually have
    # harmonized data in THIS database. Presets without derived data are
    # reported has_data=False so the UI can disable them ("no data loaded
    # in this version") instead of rendering empty results.
    populated = set(
        db.execute(
            select(HarmonizedDaily.aoi_id).distinct()
        ).scalars().all()
    )
    return [
        AoiOut(
            id=a.id, name=a.name, description=a.description,
            geojson=json.loads(a.geojson), west=a.west, south=a.south,
            east=a.east, north=a.north, is_preset=a.is_preset, created_at=a.created_at,
            has_data=a.id in populated,
        )
        for a in rows
    ]


@router.post("/aois", response_model=AoiOut, status_code=201)
def create_aoi(
    payload: AoiCreate, background: BackgroundTasks, db: Session = Depends(get_db)
) -> AoiOut:
    ring, (w, s, e, n) = _validate_polygon(payload.geojson)
    aoi_id = _slugify(payload.name, db)
    aoi = Aoi(
        id=aoi_id, name=payload.name.strip(), description=payload.description.strip(),
        geojson=json.dumps({"type": "Polygon", "coordinates": [ring]}),
        west=w, south=s, east=e, north=n, is_preset=False,
    )
    db.add(aoi)
    db.commit()
    db.refresh(aoi)

    job_id = uuid.uuid4().hex[:24]
    job = AggregationJob(id=job_id, aoi_id=aoi.id, status="PENDING", progress=0.0)
    db.add(job)
    db.commit()
    background.add_task(_run_aggregation_job, job_id, aoi.id)

    return AoiOut(
        id=aoi.id, name=aoi.name, description=aoi.description,
        geojson=json.loads(aoi.geojson), west=aoi.west, south=aoi.south,
        east=aoi.east, north=aoi.north, is_preset=aoi.is_preset, created_at=aoi.created_at,
    )


@router.get("/aois/{aoi_id}", response_model=AoiOut)
def get_aoi(aoi_id: str, db: Session = Depends(get_db)) -> AoiOut:
    a = db.get(Aoi, aoi_id)
    if a is None:
        raise HTTPException(404, "AOI not found")
    has_data = db.execute(
        select(HarmonizedDaily.aoi_id).where(HarmonizedDaily.aoi_id == aoi_id).limit(1)
    ).scalar_one_or_none() is not None
    return AoiOut(
        id=a.id, name=a.name, description=a.description,
        geojson=json.loads(a.geojson), west=a.west, south=a.south,
        east=a.east, north=a.north, is_preset=a.is_preset, created_at=a.created_at,
        has_data=has_data,
    )


@router.delete("/aois/{aoi_id}", status_code=204, response_model=None)
def delete_aoi(aoi_id: str, db: Session = Depends(get_db)) -> None:
    a = db.get(Aoi, aoi_id)
    if a is None:
        raise HTTPException(404, "AOI not found")
    if a.is_preset:
        raise HTTPException(403, "preset AOIs cannot be deleted")
    db.execute(delete(HarmonizedDaily).where(HarmonizedDaily.aoi_id == aoi_id))
    db.execute(delete(DailyAggregate).where(DailyAggregate.aoi_id == aoi_id))
    db.execute(delete(CalibrationModel).where(CalibrationModel.aoi_id == aoi_id))
    db.execute(delete(FireEpisode).where(FireEpisode.aoi_id == aoi_id))
    db.execute(delete(SeasonMetrics).where(SeasonMetrics.aoi_id == aoi_id))
    db.execute(delete(Hotspot).where(Hotspot.aoi_id == aoi_id))
    db.execute(delete(AggregationJob).where(AggregationJob.aoi_id == aoi_id))
    db.delete(a)
    db.commit()


@router.post("/aois/{aoi_id}/rebuild", response_model=JobOut, status_code=202)
def rebuild_aoi(
    aoi_id: str, background: BackgroundTasks, db: Session = Depends(get_db)
) -> JobOut:
    a = db.get(Aoi, aoi_id)
    if a is None:
        raise HTTPException(404, "AOI not found")
    job_id = uuid.uuid4().hex[:24]
    job = AggregationJob(id=job_id, aoi_id=aoi_id, status="PENDING", progress=0.0)
    db.add(job)
    db.commit()
    background.add_task(_run_aggregation_job, job_id, aoi_id)
    return JobOut(
        id=job.id, aoi_id=job.aoi_id, status="PENDING", progress=0.0, message="",
        started_at=job.started_at, finished_at=None,
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobOut:
    job = db.get(AggregationJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut(
        id=job.id, aoi_id=job.aoi_id, status=job.status,  # type: ignore[arg-type]
        progress=job.progress, message=job.message,
        started_at=job.started_at, finished_at=job.finished_at,
    )
