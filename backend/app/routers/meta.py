"""System endpoints: health and metadata (the methods panel substrate)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..harmonize.pipeline import CITATIONS
from ..ingest.firms_client import check_key
from ..models import FireEpisode, HarmonizedDaily, Hotspot, IngestRun, SystemState
from ..schemas import (
    BaselineInfo,
    DataMode,
    DataSourceInfo,
    EraInfo,
    FirmsSeam,
    FirmsStatus,
    HealthResponse,
    IngestSummary,
    MetaResponse,
    MethodologySummary,
    SensorInfo,
    StoreCounts,
)

router = APIRouter()


def _effective_mode(db: Session) -> tuple[DataMode, str]:
    cfg = get_settings()
    state = db.get(SystemState, "seed")
    if state:
        try:
            payload = json.loads(state.value_json)
            return payload.get("effective_mode", "SEED"), payload.get("seed_version", cfg.seed_version)
        except ValueError:
            pass
    return "SEED", cfg.seed_version


def _firms_status(db: Session) -> FirmsStatus:
    cfg = get_settings()
    last = db.execute(
        select(IngestRun).where(IngestRun.source == "firms_api")
        .order_by(IngestRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    reachable = None
    error = None
    if last is not None:
        if last.status in {"OK", "PARTIAL"}:
            reachable = True
        elif last.status in {"UNREACHABLE", "FAILED", "SKIPPED"}:
            reachable = False
            error = last.message[:400] or None
        try:
            meta = json.loads(last.meta_json or "{}")
        except ValueError:
            meta = {}
        if meta.get("detail"):
            error = str(meta["detail"])[:400]
    return FirmsStatus(
        key_configured=bool(cfg.firms_map_key),
        reachable=reachable,
        last_attempt_at=last.started_at if last else None,
        last_success_at=last.finished_at if (last and reachable) else None,
        last_error=error,
    )


def _firms_seams(db: Session) -> list[FirmsSeam]:
    """SP/NRT archive seams per satellite, measured from the stored record.

    The seam day is where the archive source switches from the standard
    product (reviewed science quality) to near-real-time. Counts on or after
    it are provisional: NRT rows can still be revised, and the SP product
    will eventually backfill over them. Only satellites that actually hold
    both SP and NRT rows are reported."""
    rows = db.execute(
        select(
            Hotspot.satellite,
            Hotspot.firms_product,
            func.max(Hotspot.local_date).label("last"),
            func.min(Hotspot.local_date).label("first"),
        )
        .where(Hotspot.firms_product.in_(["SP", "NRT"]))
        .group_by(Hotspot.satellite, Hotspot.firms_product)
    ).all()
    per_sat: dict[str, dict[str, date]] = {}
    for satellite, product, last, first in rows:
        per_sat.setdefault(satellite, {})[product] = first if product == "NRT" else last
    seams: list[FirmsSeam] = []
    for satellite in sorted(per_sat):
        info = per_sat[satellite]
        sp_last = info.get("SP")
        nrt_first = info.get("NRT")
        if sp_last is None or nrt_first is None:
            continue
        seams.append(FirmsSeam(
            satellite=satellite,
            sp_last_date=sp_last,
            nrt_first_date=nrt_first,
            provisional=True,
            note=(
                f"Archive source switches from standard (SP) to near-real-time "
                f"(NRT) after {sp_last.isoformat()}. Detections from "
                f"{nrt_first.isoformat()} onward are provisional: NRT is "
                "unreviewed science quality and may be revised when the "
                "standard product catches up."
            ),
        ))
    return seams


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    cfg = get_settings()
    db_ok = "ok"
    try:
        counts = _store_counts(db)
    except Exception:  # noqa: BLE001 - health must never 500 on db issues
        db_ok = "error"
        counts = StoreCounts(aois=0, hotspots=0, harmonized_days=0, episodes=0)
    effective, seed_version = _effective_mode(db)
    last_ingest = db.execute(
        select(IngestRun).order_by(IngestRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    return HealthResponse(
        status="ok" if db_ok == "ok" else "degraded",
        db=db_ok,
        data_mode=cfg.data_mode.upper(),  # type: ignore[arg-type]
        effective_data_mode=effective,  # type: ignore[arg-type]
        seed_version=seed_version,
        firms=_firms_status(db),
        counts=counts,
        last_ingest=IngestSummary(
            id=last_ingest.id, mode=last_ingest.mode, source=last_ingest.source,
            status=last_ingest.status, rows_kept=last_ingest.rows_kept,
            rows_deduped=last_ingest.rows_deduped, rows_rejected=last_ingest.rows_rejected,
            started_at=last_ingest.started_at, finished_at=last_ingest.finished_at,
            message=last_ingest.message,
        ) if last_ingest else None,
    )


def _store_counts(db: Session) -> StoreCounts:
    hotspots = int(db.scalar(select(func.count()).select_from(Hotspot)) or 0)
    harmonized_days = int(db.scalar(select(func.count()).select_from(HarmonizedDaily)) or 0)
    episodes = int(db.scalar(select(func.count()).select_from(FireEpisode)) or 0)
    aois = int(db.scalar(select(func.count()).select_from(SystemState)) or 0)
    lo = db.scalar(select(func.min(HarmonizedDaily.local_date)))
    hi = db.scalar(select(func.max(HarmonizedDaily.local_date)))
    cfg = get_settings()
    return StoreCounts(
        aois=aois if aois else 6,  # preset count until first pipeline run
        hotspots=hotspots,
        harmonized_days=harmonized_days,
        episodes=episodes,
        coverage_start=lo or cfg.seed_start,
        coverage_end=hi or cfg.seed_end,
    )


def _data_source(db: Session) -> DataSourceInfo:
    """Badge substrate derived from the DATA, not from a flag (P2b).

    kind is decided by what the hotspots table actually contains:
      FIRMS_ARCHIVE  - every row is real FIRMS (SP/NRT products set)
      SYNTHETIC_SEED - every row is the deterministic synthetic archive
      MIXED          - both populations present (live underlay over seed)
      EMPTY          - no rows
    auto_refresh is False unless a real, recent refresh job exists - the
    word LIVE is never shown for a static archive."""
    n = int(db.scalar(select(func.count()).select_from(Hotspot)) or 0)
    if n == 0:
        return DataSourceInfo(
            kind="EMPTY", label="NO DATA", n_hotspots=0,
            detail="The hotspots table is empty - run the seeder or a backfill.",
        )
    product_rows = db.execute(
        select(Hotspot.firms_product, func.count())
        .group_by(Hotspot.firms_product)
    ).all()
    products = {(p or "unattributed"): int(c) for p, c in product_rows}
    real = products.get("SP", 0) + products.get("NRT", 0)
    synthetic = products.get("unattributed", 0)
    first_acq = db.scalar(select(func.min(Hotspot.acq_date)))
    last_acq = db.scalar(select(func.max(Hotspot.acq_date)))
    last_run = db.execute(
        select(IngestRun).order_by(IngestRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    if real and synthetic:
        kind = "MIXED"
    elif real:
        kind = "FIRMS_ARCHIVE"
    else:
        kind = "SYNTHETIC_SEED"
    if kind == "FIRMS_ARCHIVE":
        # Phase 3A (defect 1): ONE badge, ONE plain wording. The label
        # carries the record range and says "not live" outright - the UI
        # renders it verbatim everywhere (there is no second badge source).
        label = (
            f"NASA FIRMS archive (real data) · "
            f"{first_acq.isoformat() if first_acq else '-'} to "
            f"{last_acq.isoformat() if last_acq else '-'} · not live"
        )
        detail = (
            f"Real NASA FIRMS archive: {products.get('SP', 0):,} SP (science "
            f"quality) + {products.get('NRT', 0):,} NRT (near-real-time, "
            f"provisional) rows, acquisitions {first_acq.isoformat() if first_acq else '-'} "
            f"to {last_acq.isoformat() if last_acq else '-'}. This is a "
            "STATIC ARCHIVE: it does not refresh automatically - re-run the "
            "backfill or the live fetch to extend it. Not a live feed."
        )
    elif kind == "MIXED":
        label = (
            f"NASA FIRMS + synthetic underlay · "
            f"{first_acq.isoformat() if first_acq else '-'} to "
            f"{last_acq.isoformat() if last_acq else '-'} · not live"
        )
        detail = (
            f"{real:,} real FIRMS rows (SP/NRT) alongside {synthetic:,} "
            "synthetic underlay rows from the deterministic seed archive. "
            "Check the per-day provenance before comparing eras. Static "
            "archive - no auto-refresh."
        )
    else:
        label = (
            f"Synthetic seed archive (not real data) · "
            f"{first_acq.isoformat() if first_acq else '-'} to "
            f"{last_acq.isoformat() if last_acq else '-'} · not live"
        )
        detail = (
            "Deterministic calibrated seed archive (synthetic, generated "
            "from documented mission physics): NOT observations. Configure "
            "FIRMS_MAP_KEY and run a backfill for the real FIRMS record. "
            "Static archive - no auto-refresh."
        )
    if last_run is not None:
        detail += (f" Last ingest run {last_run.started_at.isoformat()} "
                   f"({last_run.mode}/{last_run.status}).")
    return DataSourceInfo(
        kind=kind, label=label, auto_refresh=False, n_hotspots=n,
        products=products,
        first_acquisition=first_acq, last_acquisition=last_acq,
        last_ingest_at=last_run.started_at if last_run else None,
        last_ingest_status=last_run.status if last_run else None,
        detail=detail,
    )


@router.get("/meta", response_model=MetaResponse)
def meta(db: Session = Depends(get_db)) -> MetaResponse:
    cfg = get_settings()
    effective, seed_version = _effective_mode(db)
    data_source = _data_source(db)
    # Disclaimer follows the DATA (P2b), not the SystemState flag.
    seed_synthetic = data_source.kind in ("SYNTHETIC_SEED", "EMPTY")
    return MetaResponse(
        name="EmberEye",
        tagline="One planet, one fire record - MODIS + VIIRS harmonized since 2012.",
        version=cfg.app_version,
        seed_version=seed_version,
        data_mode=cfg.data_mode.upper(),  # type: ignore[arg-type]
        effective_data_mode=effective,  # type: ignore[arg-type]
        data_source=data_source,
        seed_disclaimer=(
            "Operating on the deterministic calibrated seed archive: a "
            "synthetic active-fire record generated from documented mission "
            "physics and published regional statistics (see the methods "
            "panel). Configure FIRMS_MAP_KEY to switch to live NASA data; "
            "every analysis method shown here runs identically on real data."
        ) if seed_synthetic else None,
        sensors=[
            SensorInfo(
                family="MODIS", satellites=["Terra", "Aqua"], resolution_m=1000,
                record_start=date(2000, 11, 1), swath_km=2330.0,
                overpass_lst=["10:30", "22:30", "13:30", "01:30"],
                confidence_scheme="numeric 0-100 (NRT) or l/n/h (SP); harmonized to LOW/NOMINAL/HIGH",
            ),
            SensorInfo(
                family="VIIRS", satellites=["SNPP", "N20", "N21"], resolution_m=375,
                record_start=date(2012, 1, 20), swath_km=3060.0,
                overpass_lst=["13:30", "01:30"],
                confidence_scheme="categorical l/n/h; harmonized to LOW/NOMINAL/HIGH",
            ),
        ],
        eras=[
            EraInfo(name="MODIS transferred", start=date(2000, 11, 1),
                    end=date(2012, 1, 19),
                    description="Comparable subset (day, non-static) expressed in VIIRS-equivalent units via smoothed month-of-year factors; carries the LOYO prediction interval."),
            EraInfo(name="VIIRS native (S-NPP reference)", start=date(2012, 1, 20),
                    end=cfg.snpp_end,
                    description="S-NPP is the reference series; NOAA-20/21 are cross-checks, never summed. Static + night layers shown separately from 2012."),
            EraInfo(name="VIIRS continuity (NOAA-20)", start=date(2026, 11, 3),
                    end=date(2099, 12, 31),
                    description="Post S-NPP end of delivery (2026-11-02): NOAA-20 / day-night continuity factors (direction: successor / factor) keep the series in S-NPP-equivalent units."),
        ],
        firms_seams=_firms_seams(db),
        baseline=BaselineInfo(
            start_year=cfg.baseline_start, end_year=cfg.baseline_end,
            kernel_days=15, z_threshold=cfg.anomaly_z_threshold,
            definition=("Day-of-year climatology of the comparable-subset series over the fixed "
                        f"{cfg.baseline_start}-{cfg.baseline_end} baseline with a +/-15-day kernel; "
                        "anomaly flags are leave-one-year-out EMPIRICAL percentiles (p >= 95 high; "
                        "zero against an active baseline = unusually quiet) with a min-count guard of "
                        f"{cfg.anomaly_min_baseline_years} baseline years."),
        ),
        methodology=MethodologySummary(
            harmonization_tier="Phase 2 comparable-subset transfer (month-of-year factors, LOYO-validated) + Tier-2 diagnostics (pairing, dedup) + Tier-3 episode clustering",
            steps=[
                "Normalize both FIRMS records to one canonical schema (confidence ordinalization, UTC->local-day bucketing, pixel geometry).",
                "Per-sensor duplicate correction (Zheng et al. 2026 rule adapted to FIRMS minute granularity).",
                "Near-synchronous Aqua MODIS <-> S-NPP VIIRS footprint pairing (|dt| <= 5 min) as cross-calibration evidence.",
                "COMPARABLE SUBSET (daytime, non-static): smoothed month-of-year transfer factors calibrated 2012-2022, leave-one-year-out validated (MAPE, bias, 95% prediction interval carried into the series and UI). The monthly power-law is retired to a Methods comparison column.",
                "Era-clean daily series with Intra-VIIRS Constellation Normalization: S-NPP serves as the single-reference baseline (2012-2026); multi-satellite overpasses from NOAA-20 (2018+) and NOAA-21 (2024+) are cross-calibrated and never raw-summed (preventing 2x-3x step changes). Swath gaps and post-S-NPP decommissioning (Nov 2026) are normalized via empirical overlap continuity scaling: n_h = n_NOAA20 / f_day, where f_day = sum(N_NOAA20)/sum(N_SNPP).",
                "Separate static + night layers from 2012 (no MODIS equivalent exists before); included in totals only where the layer is switched on; static share always displayed.",
                "DOY climatology (+/-15 d kernel, fixed baseline) with leave-one-year-out empirical percentile anomalies and a min-count guard.",
                "Deterministic spatio-temporal episode clustering (Su & Yu 2026 style) and fire-season metrics with season censoring.",
            ],
            units="Harmonized units are S-NPP-equivalent comparable-subset detections (day, non-static); the interval columns carry the transfer uncertainty.",
        ),
        citations=CITATIONS,
    )
