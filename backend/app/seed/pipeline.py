"""Seeding orchestration: presets, mode decision, full pipeline rebuild.

Idempotency: a completed seed run for the current SEED_VERSION is detected
via SystemState and skipped unless FORCE_SEED=1. The seed archive is
deterministic, so "skip" and "rebuild" converge to the same database.

REAL-DATA GUARD (v1.0.4): when the database already holds real FIRMS rows
(`hotspots.firms_product` is SP/NRT; synthetic rows carry ""), the seeder
REFUSES to run - synthetic seeding REPLACES the AOI's rows
(`replace_aoi_records` wipes first), and the LIVE mode lays a synthetic
underlay alongside real data. Real rows are never wiped or mixed unless the
operator passes FORCE_SEED=1 AND EMBEREBEYE_ALLOW_SEED_OVER_REAL=1.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..geometry import ring_bbox
from ..harmonize.pipeline import CITATIONS, run_pipeline_for_aoi
from ..ingest.canonical import CanonicalRecord
from ..ingest.dedup import deduplicate
from ..ingest.firms_client import check_key
from ..ingest.persist import apply_static_source_mask, create_run, finish_run, replace_aoi_records
from ..models import Aoi, HarmonizedDaily, Hotspot, IngestRun, SystemState
from .profiles import PRESET_PROFILES, PROFILES_BY_ID
from .synth import generate_aoi_records

log = logging.getLogger("embereye.seed")

AOI_GEOJSON_TEMPLATE = (
    '{{"type": "Polygon", "coordinates": [[[{w},{s}],[{e},{s}],[{e},{n}],[{w},{n}],[{w},{s}]]]}}'
)


def ensure_preset_aois(db: Session) -> list[Aoi]:
    out: list[Aoi] = []
    for profile in PRESET_PROFILES:
        aoi = db.get(Aoi, profile.aoi_id)
        if aoi is None:
            w, s, e, n = profile.bbox
            geojson = json.loads(AOI_GEOJSON_TEMPLATE.format(w=w, s=s, e=e, n=n))
            aoi = Aoi(
                id=profile.aoi_id, name=profile.name,
                description=profile.description, geojson=json.dumps(geojson),
                west=w, south=s, east=e, north=n, is_preset=True,
            )
            db.add(aoi)
            db.commit()
            db.refresh(aoi)
        out.append(aoi)
    return out


def seed_completed(db: Session, seed_version: str) -> bool:
    state = db.get(SystemState, "seed")
    if state is None:
        return False
    try:
        payload = json.loads(state.value_json)
    except ValueError:
        return False
    if payload.get("seed_version") != seed_version:
        return False
    if payload.get("status") != "OK":
        return False
    n_hotspots = db.scalar(select(func.count()).select_from(Hotspot))
    return bool(n_hotspots and n_hotspots > 0)


def count_real_hotspots(db: Session) -> dict:
    """Real FIRMS rows per AOI (synthetic rows carry firms_product='').

    Any count > 0 means the database holds data that seeding must not
    destroy or mix with the synthetic archive.
    """
    rows = db.execute(
        select(Hotspot.aoi_id, func.count())
        .where(Hotspot.firms_product.in_(["SP", "NRT"]))
        .group_by(Hotspot.aoi_id)
    ).all()
    return {aoi_id: int(n) for aoi_id, n in rows}


def decide_mode(db: Session) -> tuple[str, str]:
    """-> (effective_mode, explanation). Never raises."""
    cfg = get_settings()
    mode = cfg.data_mode.upper()
    if mode == "SEED":
        return "SEED", "DATA_MODE=SEED: using the deterministic calibrated archive."
    if mode == "LIVE":
        if not cfg.firms_map_key:
            return "SEED", "DATA_MODE=LIVE but FIRMS_MAP_KEY missing; falling back to seed."
        probe = check_key()
        if not probe.get("reachable", False):
            return "SEED", f"DATA_MODE=LIVE but FIRMS unreachable ({probe.get('detail')}); seed fallback."
        return "LIVE", "DATA_MODE=LIVE: live FIRMS ingest."
    # AUTO
    if not cfg.firms_map_key:
        return "SEED", "No FIRMS_MAP_KEY configured: deterministic seed archive."
    probe = check_key()
    if not probe.get("reachable", False):
        return "SEED", f"FIRMS unreachable ({probe.get('detail')}): seed fallback engaged."
    return "LIVE", "FIRMS reachable: live ingest enabled."


def _seed_synthetic(db: Session, aoi_list: list[Aoi], scale: float) -> dict:
    cfg = get_settings()
    t0 = time.monotonic()
    total_rows = 0
    per_aoi: dict[str, dict] = {}
    for i, aoi in enumerate(aoi_list):
        run = create_run(db, mode="SEED", source="synthetic", aoi_id=aoi.id)
        records: list[CanonicalRecord] = generate_aoi_records(
            PROFILES_BY_ID.get(aoi.id) or _profile_from_aoi(aoi),
            aoi.id, cfg.seed_start, cfg.seed_end, scale, cfg.seed_version,
        )
        kept, deduped = deduplicate(
            records, cfg.dedup_max_gap_minutes,
            cfg.dedup_max_distance_km_modis, cfg.dedup_max_distance_km_viirs,
        )
        replace_aoi_records(db, aoi.id, kept, run_id=run.id)
        flagged = apply_static_source_mask(
            db, aoi.id, cfg.static_source_min_days, cfg.static_source_cell_deg
        )
        finish_run(db, run, "OK", "synthetic archive generated",
                   rows_in=len(records), rows_kept=len(kept), rows_deduped=deduped,
                   rows_rejected=len(records) - len(kept) - deduped,
                   meta={"static_source_flagged": flagged})
        total_rows += len(kept)
        per_aoi[aoi.id] = {"rows": len(kept), "deduped": deduped, "static_flagged": flagged}
        log.info("seed %s: %d rows kept, %d deduped, %d static-flagged",
                 aoi.id, len(kept), deduped, flagged)
    log.info("synthetic archive complete: %d rows in %.1fs", total_rows, time.monotonic() - t0)
    return {"total_rows": total_rows, "per_aoi": per_aoi}


def _profile_from_aoi(aoi: Aoi):
    """Fallback profile for user-created AOIs during a full reseed: a sparse
    background regime derived from the stored geometry. Only reachable via
    FORCE_SEED rebuilds; user AOIs normally keep their ingested rows."""
    from .profiles import RegionProfile

    ring = json.loads(aoi.geojson)["coordinates"][0]
    w, s, e, n = ring_bbox(ring)
    return RegionProfile(
        aoi_id=aoi.id, name=aoi.name, description="user AOI (background regime)",
        bbox=(w, s, e, n),
        clusters=(),
        base_daily_events=0.0,
        seasonality=((210, 60, 1.0),),
        year_factors={},
        trend_per_year=0.0,
        det_prob_modis=(0.04, 0.4, 0.97),
        det_prob_viirs=(0.46, 0.93, 1.0),
        frp_lognorm=(1.6, 1.0),
        saturation_prob_viirs=0.08,
        cloud_seasonality=((15, 40, 0.5),),
        static_source_cells=(),
        equatorial_gap_factor=0.2,
        size_probs=(0.6, 0.3, 0.1),
        day_fraction=0.5,
    )


def _live_ingest_all(db: Session, aoi_list: list[Aoi]) -> dict:
    from .live_ingest import ingest_firms_for_aoi, latest_available_day

    out: dict = {}
    for aoi in aoi_list:
        res = ingest_firms_for_aoi(
            db, aoi, day_range=5, day=latest_available_day()
        )
        out[aoi.id] = {"status": res["status"], "rows": res["inserted"]}
        log.info("live ingest %s: %s (%d rows)", aoi.id, res["status"], res["inserted"])
    return out


def run_seed(force: bool = False) -> dict:
    """Entry point for the seeder container and local dev. Returns a report."""
    from ..db import SessionLocal, create_all

    logging.basicConfig(level=get_settings().log_level,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = get_settings()
    create_all()
    db = SessionLocal()
    try:
        aoi_list = ensure_preset_aois(db)
        if not force and seed_completed(db, cfg.seed_version):
            log.info("seed version %s already present; skipping (FORCE_SEED=1 to rebuild)",
                     cfg.seed_version)
            return {"status": "SKIPPED", "reason": "already-seeded"}

        # ---- REAL-DATA GUARD ---------------------------------------------
        # Seeding wipes each AOI (replace_aoi_records) and/or lays a synthetic
        # underlay - both are destructive or polluting next to a real FIRMS
        # backfill. Refuse loudly instead of silently replacing 171k real
        # rows with synthetic ones.
        real = count_real_hotspots(db)
        if real:
            override = (
                force
                and os.environ.get("EMBEREBEYE_ALLOW_SEED_OVER_REAL", "") == "1"
            )
            breakdown = ", ".join(f"{k}={v:,}" for k, v in sorted(real.items()))
            if not override:
                log.warning(
                    "REAL DATA PRESENT - seeding refused: %s real FIRMS rows (%s). "
                    "The seeder would replace/mix them with the synthetic archive. "
                    "If you really mean it: FORCE_SEED=1 AND "
                    "EMBEREBEYE_ALLOW_SEED_OVER_REAL=1 (and back up first - "
                    "see docs/BACKUP.md).",
                    sum(real.values()), breakdown)
                return {"status": "REFUSED", "reason": "real-data-present",
                        "real_rows": real}
            log.warning(
                "REAL DATA WILL BE REPLACED (FORCE_SEED=1 + ALLOW override): %s",
                breakdown)

        effective, explanation = decide_mode(db)
        log.info("data mode: %s (%s)", effective, explanation)
        scale = cfg.seed_scale
        if effective == "LIVE":
            live_report = _live_ingest_all(db, aoi_list)
            # Live data covers only the recent window: the deterministic
            # archive is still laid down underneath for the 2012+ history.
            synth_report = _seed_synthetic(db, aoi_list, scale)
            pipeline_report = _pipeline_all(db, aoi_list)
            report = {
                "status": "OK", "effective_mode": "LIVE",
                "explanation": explanation,
                "live": live_report, "synthetic_underlay": synth_report,
                "pipeline": pipeline_report,
            }
        else:
            synth_report = _seed_synthetic(db, aoi_list, scale)
            pipeline_report = _pipeline_all(db, aoi_list)
            report = {
                "status": "OK", "effective_mode": "SEED",
                "explanation": explanation,
                "synthetic": synth_report, "pipeline": pipeline_report,
            }

        state = db.get(SystemState, "seed")
        payload = json.dumps({
            "seed_version": cfg.seed_version,
            "status": "OK",
            "effective_mode": report["effective_mode"],
            "coverage": [cfg.seed_start.isoformat(), cfg.seed_end.isoformat()],
            "citations": CITATIONS,
        }, sort_keys=True)
        if state is None:
            db.add(SystemState(key="seed", value_json=payload))
        else:
            state.value_json = payload
        db.commit()
        log.info("seed complete: %s", json.dumps({k: v for k, v in report.items() if k != "synthetic_underlay"})[:400])
        return report
    finally:
        db.close()


def seed_unseeded_presets(db: Session, force: bool = False) -> dict:
    """Populate any preset AOI that currently lacks data without touching real records.

    Preserves any AOI with real FIRMS data (e.g. Nile Delta's 171k rows).
    Generates deterministic, scientifically grounded multi-era records for the
    remaining presets from their calibrated profiles in profiles.py, then runs
    the full harmonization pipeline for each.
    """
    cfg = get_settings()
    aoi_list = ensure_preset_aois(db)
    real_aois = set(count_real_hotspots(db).keys())
    scale = cfg.seed_scale
    seeded_count = 0
    results: dict[str, dict] = {}

    for aoi in aoi_list:
        if aoi.id in real_aois and not (force and os.environ.get("EMBEREYE_ALLOW_SEED_OVER_REAL") == "1"):
            log.info("AOI %s has %d real FIRMS rows; skipping synthetic generation.",
                     aoi.id, count_real_hotspots(db)[aoi.id])
            results[aoi.id] = {"status": "SKIPPED_REAL_DATA", "aoi_id": aoi.id}
            continue

        existing_hotspots = db.scalar(
            select(func.count(Hotspot.id)).where(Hotspot.aoi_id == aoi.id)
        ) or 0
        existing_harmonized = db.scalar(
            select(func.count(HarmonizedDaily.local_date)).where(HarmonizedDaily.aoi_id == aoi.id)
        ) or 0

        if existing_hotspots > 0 and existing_harmonized >= 9000 and not force:
            log.info("AOI %s already seeded (%d hotspots, %d harmonized); skipping.",
                     aoi.id, existing_hotspots, existing_harmonized)
            results[aoi.id] = {"status": "ALREADY_SEEDED", "aoi_id": aoi.id,
                               "hotspots": existing_hotspots, "harmonized": existing_harmonized}
            continue

        profile = PROFILES_BY_ID.get(aoi.id) or _profile_from_aoi(aoi)
        log.info("Seeding preset %s from profile '%s' (bbox=%s)...", aoi.id, profile.name, profile.bbox)

        run = create_run(db, mode="SEED", source="synthetic", aoi_id=aoi.id)
        records: list[CanonicalRecord] = generate_aoi_records(
            profile, aoi.id, cfg.seed_start, cfg.seed_end, scale, cfg.seed_version,
        )
        kept, deduped = deduplicate(
            records, cfg.dedup_max_gap_minutes,
            cfg.dedup_max_distance_km_modis, cfg.dedup_max_distance_km_viirs,
        )
        replace_aoi_records(db, aoi.id, kept, run_id=run.id)
        flagged = apply_static_source_mask(
            db, aoi.id, cfg.static_source_min_days, cfg.static_source_cell_deg
        )
        finish_run(db, run, "OK", "synthetic archive generated",
                   rows_in=len(records), rows_kept=len(kept), rows_deduped=deduped,
                   rows_rejected=len(records) - len(kept) - deduped,
                   meta={"static_source_flagged": flagged})

        # Run harmonization pipeline
        log.info("Running harmonization pipeline for %s...", aoi.id)
        summary = run_pipeline_for_aoi(db, aoi)
        seeded_count += 1
        results[aoi.id] = {
            "status": "SEEDED_AND_HARMONIZED",
            "hotspots_kept": len(kept),
            "deduped": deduped,
            "static_flagged": flagged,
            "pipeline": summary,
        }

    return {"status": "OK", "seeded_presets": seeded_count, "results": results}



def _pipeline_all(db: Session, aoi_list: list[Aoi]) -> dict:
    out: dict = {}
    for i, aoi in enumerate(aoi_list, start=1):
        log.info("harmonization pipeline %d/%d: %s", i, len(aoi_list), aoi.id)
        t0 = time.monotonic()
        summary = run_pipeline_for_aoi(
            db, aoi,
            progress=lambda frac, msg="", aid=aoi.id: log.info("  [%s] %3.0f%% %s", aid, frac * 100, msg),
        )
        out[aoi.id] = summary
        log.info("  %s done in %.1fs", aoi.id, time.monotonic() - t0)
    return out
