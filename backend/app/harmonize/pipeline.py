"""Per-AOI harmonization pipeline orchestrator (Phase 2).

Stages (each deterministic, each independently testable):
  1. load canonical hotspot arrays
  2. materialize per-(day, satellite) aggregates
  3. near-synchronous footprint pairing (Aqua <-> S-NPP diagnostics)
  4. fit the Phase 2 month-of-year transfer on the COMPARABLE SUBSET
     (daytime, non-static), LOYO-validated; power-law kept as comparison;
     constellation continuity factors (direction: successor / factor)
  5. build the era-clean harmonized series + DOY climatology + LOYO
     empirical anomaly flags
  6. cluster fire episodes (static sources excluded)
  7. fire-season metrics per year (with season censoring)

Writes are idempotent per AOI: prior derived rows are cleared, then
replaced. A progress callback keeps the aggregation job API honest.

Phase 2 design (audit-approved 2026-11-02):
  * headline population = daytime, non-static detections ("comparable
    subset"); static + night layers are separate from 2012 and never
    silently hidden;
  * 2000-2011: MODIS (Terra+Aqua) -> VIIRS-equivalent via smoothed
    month-of-year factors calibrated 2012-2022, with the LOYO prediction
    interval carried into harmonized_daily;
  * 2012 onward: native S-NPP-reference VIIRS (N20/N21 never summed);
  * after S-NPP end of delivery (2026-11-02): NOAA-20 / continuity factor
    (day/night-specific, factor > 1 => divide).
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    CalibrationModel,
    DailyAggregate,
    FireEpisode,
    HarmonizedDaily,
    SeasonMetrics,
    SystemState,
)
from .calibration import TransferFit, fit_transfer
from .climatology import (
    SeasonCard,
    anomaly_flags,
    build_climatology,
    loyo_percentile_flags,
    season_metrics,
)
from .episodes import cluster_episodes
from .matching import pair_near_synchronous
from .moy import fit_moy_transfer
from .series import (
    SAT_NAMES,
    continuity_factors,
    daily_aggregates,
    harmonize_comparable,
    load_aoi_arrays,
    load_arrays_spatial,
)

CITATIONS = [
    "Giglio, Schroeder & Justice (2016) RSE 178:31-43 - MODIS C6 active fire algorithm",
    "Schroeder, Oliva, Giglio & Csiszar (2014) RSE 143:85-96 - VIIRS 375 m active fire product",
    "Zheng et al. (2026) Remote Sensing 18(10):1650 - FRP correction and spatiotemporal fusion",
    "Vadrevu et al. (2018) Remote Sensing 10(7):978 - MODIS Aqua / VIIRS I-band intercomparison",
    "Li, Zhang, Kondragunta & Csiszar (2018) JGR-Atmospheres 123:4545-4563 - VIIRS vs MODIS FRP",
    "Wooster, Zhukov & Oertel (2003) RSE 86:83-107 - FRP retrieval",
    "Su & Yu (2026) ESSD preprint doi:10.5194/essd-2026-387 - individual fire events from VIIRS",
    "Jolly et al. (2015) Nature Communications 6:8537 - fire-weather season length",
]

LIMITATIONS = [
    # P3 (2026-11-02 handoff): harmonization assumptions, stated explicitly.
    "STATIONARITY ASSUMPTION: the MODIS-to-VIIRS relation is calibrated on "
    "2012-2022 and is ASSUMED STATIONARY when applied backwards to "
    "2000-2011. It cannot be validated with VIIRS observations before "
    "2012-01-20 (no VIIRS data exists). Sensor behaviour, algorithms and "
    "fire regimes may have shifted in that period; the transferred "
    "2000-2011 segment carries the LOYO interval but no independent "
    " VIIRS-era check is possible.",
    "The ONLY independent check available for the pre-2012 segment is "
    "MCD64A1 / BA_MODIS burned area (available from 2000): a coarse, "
    "complementary product (monthly burned area, not active fire) that can "
    "corroborate seasonality and inter-annual ranking, not detection "
    "counts. It is not yet ingested; any pre-2012 count-level claim is "
    "therefore model-dependent.",
    "Terra-only era 2000-11..2002-06: before Aqua's launch the comparable "
    "MODIS record is Terra alone, so the Terra-specific month-of-year "
    "factor is applied there (the pooled factor calibrates the "
    "two-satellite mix). Fewer overpasses per day mean lower detection "
    "probability than the Terra+Aqua era - a step-change artifact at "
    "2002-06, not a fire-regime change.",
    "Terra/Aqua orbit drift from 2023 (overpass hours Terra 8.8->7.1 UTC, "
    "Aqua 11.2->13.1 UTC): MODIS after 2022 is excluded from calibration and "
    "from the harmonized comparison; the drift direction inflates the "
    "VIIRS/MODIS ratio post-2022.",
    "The 2018 daytime drop (-36% to -71% vs 2017) appears in all three "
    "independent sensors in daytime while night counts stay flat; cause "
    "unknown - do not speculate. 2018 is left in the record uncorrected.",
    "The SP/NRT seam (MODIS SP ends 2026-08-31, VIIRS SP 2026-07-31): NRT "
    "rows are unreviewed science quality and the recent months are "
    "provisional; a later FIRMS SP extension replaces NRT-era rows on "
    "re-run of the backfill.",
    "S-NPP end of delivery 2026-11-02: from that date the series continues "
    "on NOAA-20 divided by day/night continuity factors fitted on the "
    "103 shared months (direction: successor / factor).",
    "All numbers are for the selected AOI only (presets: nile-delta-egypt); "
    "factors and percentiles do not transfer to other regions.",
    "Static (type=2) and persistence-tagged sources stay in totals per the "
    "challenge text (they are burning activity); the static share is always "
    "displayed, never hidden silently.",
]


def comparable_monthly_pairs(arr, years: list[int]) -> dict:
    """Comparable-subset (day, non-static) monthly pairs per platform group.

    Returns {pooled: {year: {moy: (modis, snpp)}}, terra: ..., aqua: ...}
    with every month of the year present (zeros included) so LOYO
    predictions sum over complete calendar years.
    """
    is_day = arr.daynight == 0
    comp = is_day & ~arr.static_source
    is_terra = comp & (arr.satellite == 0)
    is_aqua = comp & (arr.satellite == 1)
    is_snpp = comp & (arr.satellite == 2)

    ord_month = np.array(
        [date.fromordinal(int(o)).month for o in arr.date_ord], dtype=np.int64
    )
    ord_year = np.array(
        [date.fromordinal(int(o)).year for o in arr.date_ord], dtype=np.int64
    )

    out: dict[str, dict] = {"pooled": {}, "terra": {}, "aqua": {}}
    for y in years:
        sel_y = ord_year == y
        for name, m_mask in (
            ("pooled", is_terra | is_aqua),
            ("terra", is_terra),
            ("aqua", is_aqua),
        ):
            months = {}
            for m in range(1, 13):
                sel = sel_y & (ord_month == m)
                months[m] = (
                    float(np.count_nonzero(m_mask & sel)),
                    float(np.count_nonzero(is_snpp & sel)),
                )
            out[name][y] = months
    return out


def comparable_daily_pairs(arr, years: list[int]) -> dict[int, tuple]:
    """Comparable-subset DAILY counts per year for the ISO-week LOYO.

    Returns {year: (date_ords int64[n], modis_counts f8[n], viirs_counts
    f8[n])} where modis = pooled comparable Terra+Aqua and viirs = comparable
    reference-platform (S-NPP) counts of that local day.
    """
    is_day = arr.daynight == 0
    comp = is_day & ~arr.static_source
    is_modis = comp & ((arr.satellite == 0) | (arr.satellite == 1))
    is_snpp = comp & (arr.satellite == 2)
    ord_year = np.array(
        [date.fromordinal(int(o)).year for o in arr.date_ord], dtype=np.int64
    )
    out: dict[int, tuple] = {}
    for y in years:
        sel = ord_year == y
        ords = arr.date_ord[sel]
        m = np.bincount(
            ords - min(ords), weights=is_modis[sel].astype(np.float64),
            minlength=int(ords.max() - ords.min()) + 1,
        ) if ords.size else np.zeros(0)
        v = np.bincount(
            ords - min(ords), weights=is_snpp[sel].astype(np.float64),
            minlength=int(ords.max() - ords.min()) + 1,
        ) if ords.size else np.zeros(0)
        uniq_ords = np.arange(int(ords.min()), int(ords.max()) + 1) if ords.size else np.zeros(0, dtype=np.int64)
        out[y] = (uniq_ords, m, v)
    return out


def run_pipeline_for_aoi(
    db: Session,
    aoi,  # models.Aoi
    progress=None,
) -> dict:
    """Full derived-data rebuild for one AOI. Returns a methods summary.

    Record loading is spatially aware: preset AOIs read their aoi-scoped
    rows; user-drawn AOIs run a bbox prefilter plus exact point-in-polygon
    over the canonical record store.
    """
    cfg = get_settings()
    report = progress or (lambda frac, msg="": None)
    aoi_id = aoi.id

    report(0.05, "loading hotspot arrays")
    from .series import load_aoi_arrays, load_arrays_spatial

    if getattr(aoi, "is_preset", False):
        arr = load_aoi_arrays(db, aoi_id)
    else:
        arr = load_arrays_spatial(db, aoi)

    db.execute(delete(DailyAggregate).where(DailyAggregate.aoi_id == aoi_id))
    db.execute(delete(CalibrationModel).where(CalibrationModel.aoi_id == aoi_id))
    db.execute(delete(HarmonizedDaily).where(HarmonizedDaily.aoi_id == aoi_id))
    db.execute(delete(FireEpisode).where(FireEpisode.aoi_id == aoi_id))
    db.execute(delete(SeasonMetrics).where(SeasonMetrics.aoi_id == aoi_id))
    db.flush()

    # ---- 2. daily aggregates ------------------------------------------------
    report(0.12, "daily aggregates")
    agg = daily_aggregates(arr)
    for i in range(agg.date_ord.size):
        db.add(DailyAggregate(
            aoi_id=aoi_id,
            local_date=date.fromordinal(int(agg.date_ord[i])),
            satellite=SAT_NAMES.get(int(agg.satellite[i]), "UNK"),
            sensor_family="MODIS" if agg.sensor[i] == 0 else "VIIRS",
            n_detections=int(agg.n_total[i]),
            n_strict=int(agg.n_strict[i]),
            n_day=int(agg.n_day[i]),
            n_night=int(agg.n_night[i]),
            frp_sum_mw=float(agg.frp_sum[i]),
            frp_mean_mw=float(agg.frp_mean[i]),
            pixel_area_sum_km2=float(agg.area_sum[i]),
            t4_mean_k=float(agg.t4_mean[i]),
        ))
    db.flush()

    if len(arr) == 0:
        db.commit()
        report(1.0, "no data for AOI")
        return {"status": "NO_DATA"}

    # ---- 3. near-synchronous pairing ---------------------------------------
    report(0.25, "near-synchronous pairing")
    modis_sub = arr.subset(arr.sensor == 0)
    viirs_sub = arr.subset(arr.sensor == 1)
    pairing_stats = pair_near_synchronous(
        {"t_unix": modis_sub.t_unix, "lat": modis_sub.lat, "lon": modis_sub.lon,
         "frp_mw": modis_sub.frp_mw, "area_km2": modis_sub.area_km2,
         "satellite": modis_sub.satellite},
        {"t_unix": viirs_sub.t_unix, "lat": viirs_sub.lat, "lon": viirs_sub.lon,
         "frp_mw": viirs_sub.frp_mw, "area_km2": viirs_sub.area_km2,
         "satellite": viirs_sub.satellite},
        max_dt_minutes=cfg.pair_max_delta_minutes,
    )

    # ---- 4. Phase 2 calibration on the comparable subset --------------------
    report(0.40, "fitting month-of-year transfer (comparable subset)")
    all_years = sorted({date.fromordinal(int(o)).year for o in np.unique(arr.date_ord)})
    cal_years = [y for y in all_years
                 if cfg.calibration_start <= y <= cfg.calibration_end]
    has_snpp = bool(np.count_nonzero(arr.satellite == 2))
    transfer = None
    frp_transfer = None
    comparison_fit = None
    agreement = 0.0
    if has_snpp and len(cal_years) >= 3:
        pairs = comparable_monthly_pairs(arr, cal_years)
        daily = comparable_daily_pairs(arr, cal_years)
        transfer = fit_moy_transfer(
            pairs["pooled"], pairs["terra"], pairs["aqua"],
            min_month_total=cfg.moy_min_month_total,
            per_platform_margin=cfg.moy_per_platform_margin,
            daily_by_year=daily,
        )
        frp_pairs = _frp_monthly_pairs(arr, cal_years)
        frp_transfer = fit_moy_transfer(
            frp_pairs["pooled"], frp_pairs["terra"], frp_pairs["aqua"],
            min_month_total=0.0, per_platform_margin=cfg.moy_per_platform_margin,
        )
        # Retired power-law, kept as a Methods comparison column.
        comparison_fit = _fit_power_law_comparison(pairs)
        agreement = _monthly_agreement(pairs["pooled"])

    # Continuity factors (direction check: factor > 1 => successor HIGHER,
    # reference-equivalent = successor / factor).
    cont_n20 = continuity_factors(arr, reference_code=2, successor_code=3)
    cont_n21 = continuity_factors(arr, reference_code=2, successor_code=4)

    def _store_fit(variable: str, method: str, slope: float, intercept: float,
                   window: tuple[date, date], n_months: int, params: dict,
                   agreement_stat: float = 0.0) -> None:
        db.add(CalibrationModel(
            aoi_id=aoi_id, variable=variable, direction="MODIS_TO_VIIRS",
            method=method, slope=float(slope), intercept=float(intercept),
            r2_in_sample=0.0, r2_oos=0.0, r2_raw_oos=0.0,
            n_months=int(n_months), n_pairs=int(n_months),
            window_start=window[0], window_end=window[1],
            agreement_stat=float(agreement_stat),
            params_json=json.dumps(params, sort_keys=True),
        ))

    first_data = date.fromordinal(int(arr.date_ord.min()))
    last_data = date.fromordinal(int(arr.date_ord.max()))
    window = (max(first_data, date(cfg.calibration_start, 1, 1)),
              min(last_data, date(cfg.calibration_end, 12, 31)))

    if transfer is not None:
        _store_fit("COUNTS", "moy_factor_v1", transfer.pooled.annual_ratio, 0.0,
                   window, len(cal_years) * 12, transfer.as_params(), agreement)
    else:
        # Deterministic fallback for records without a calibratable overlap:
        # raw comparable counts pass through unscaled (flagged, never silent).
        _store_fit("COUNTS", "passthrough_fallback", 1.0, 0.0, window, 0,
                   {"reason": "no calibratable VIIRS overlap on this AOI"})
    if frp_transfer is not None:
        _store_fit("FRP", "moy_factor_v1", frp_transfer.pooled.annual_ratio, 0.0,
                   window, len(cal_years) * 12, frp_transfer.as_params(), 0.0)
    if comparison_fit is not None:
        _store_fit("COUNTS", "theil_sen_log1p", comparison_fit.slope,
                   comparison_fit.intercept, window, comparison_fit.n_months,
                   {"role": "comparison_retired", "loyo_bias_legacy":
                        "see docs/AUDIT_REAL.md A7 (-15% to -23%)"})

    for label, cont in (("NOAA20_TO_SNPP", cont_n20), ("NOAA21_TO_SNPP", cont_n21)):
        if cont.get("factor_day") or cont.get("factor_night"):
            db.add(CalibrationModel(
                aoi_id=aoi_id, variable="COUNTS", direction=label,
                method="continuity_ratio", slope=1.0, intercept=0.0,
                r2_in_sample=0.0, r2_oos=0.0, r2_raw_oos=0.0,
                n_months=int(cont.get("shared_months") or 0), n_pairs=0,
                window_start=first_data, window_end=last_data,
                agreement_stat=0.0,
                params_json=json.dumps({
                    "factor_day": cont.get("factor_day"),
                    "factor_night": cont.get("factor_night"),
                    "factor_day_lo": cont.get("factor_day_lo"),
                    "factor_day_hi": cont.get("factor_day_hi"),
                    "factor_night_lo": cont.get("factor_night_lo"),
                    "factor_night_hi": cont.get("factor_night_hi"),
                    "factor_lo": cont.get("factor_lo"),
                    "factor_hi": cont.get("factor_hi"),
                    "per_year": cont.get("per_year", {}),
                    "per_month": cont.get("per_month", {}),
                    "direction": "SNPP_equiv = successor / factor "
                                 "(factor > 1 means successor counts higher)",
                    "shared_months": cont.get("shared_months"),
                    "shared_months_day": cont.get("shared_months_day"),
                    "shared_months_night": cont.get("shared_months_night"),
                }, sort_keys=True),
            ))
    db.flush()

    # ---- 5. harmonized series + climatology + LOYO anomalies ----------------
    report(0.55, "harmonized series (comparable subset)")
    snpp_mask = arr.satellite == 2
    if snpp_mask.any():
        viirs_start_ord = int(arr.date_ord[snpp_mask].min())
    else:
        viirs_mask = arr.satellite >= 2
        viirs_start_ord = (int(arr.date_ord[viirs_mask].min())
                           if viirs_mask.any() else last_data.toordinal() + 1)
    snpp_end_ord = cfg.snpp_end.toordinal()

    ledger = harmonize_comparable(
        arr, int(arr.date_ord.min()), int(arr.date_ord.max()),
        transfer, frp_transfer, viirs_start_ord,
        reference_code=2, successor_code=3,
        snpp_end_ord=snpp_end_ord,
        cont=(cont_n20 if cont_n20.get("factor_day")
              or cont_n20.get("factor_night") else None),
    )
    date_ords = ledger["date_ord"]
    n_h = ledger["n_harmonized"]
    frp_h = ledger["frp_harmonized"]
    clim = build_climatology(
        date_ords, n_h, frp_h, (cfg.baseline_start, cfg.baseline_end), kernel_days=15,
    )
    flags = loyo_percentile_flags(
        date_ords, n_h,
        (cfg.baseline_start, cfg.baseline_end),
        kernel_days=15, min_years=cfg.anomaly_min_baseline_years,
        pct_high=95.0,
    )
    # FRP z keeps the parametric DOY climatology (no LOYO requirement there).
    frp_flags = anomaly_flags(clim, date_ords, n_h, frp_h, cfg.anomaly_z_threshold)

    for i in range(date_ords.size):
        share = ledger["static_share"][i]
        db.add(HarmonizedDaily(
            aoi_id=aoi_id,
            local_date=date.fromordinal(int(date_ords[i])),
            n_modis=int(ledger["n_modis"][i]),
            n_viirs=int(ledger["n_viirs"][i]),
            n_comparable_modis=int(ledger["n_comparable_modis"][i]),
            n_comparable_viirs=int(ledger["n_comparable_viirs"][i]),
            n_harmonized=float(n_h[i]),
            n_harmonized_lo=float(ledger["n_harmonized_lo"][i]),
            n_harmonized_hi=float(ledger["n_harmonized_hi"][i]),
            frp_modis_mw=float(ledger["frp_modis"][i]),
            frp_viirs_mw=float(ledger["frp_viirs"][i]),
            frp_harmonized_mw=float(frp_h[i]),
            frp_harmonized_lo_mw=float(ledger["frp_harmonized_lo"][i]),
            frp_harmonized_hi_mw=float(ledger["frp_harmonized_hi"][i]),
            n_static=int(ledger["n_static"][i]),
            n_night=int(ledger["n_night"][i]),
            static_share=(None if not np.isfinite(share) else float(share)),
            seam_weight=float(ledger["era"][i]),
            provenance=str(ledger["provenance"][i]),
            z_n=(None if not np.isfinite(flags["z_n"][i]) else float(flags["z_n"][i])),
            z_frp=(None if not np.isfinite(frp_flags["z_frp"][i])
                   else float(frp_flags["z_frp"][i])),
            pct_n=(None if not np.isfinite(flags["pct_n"][i]) else float(flags["pct_n"][i])),
            flag_high=bool(flags["flag_high"][i]),
            flag_low=bool(flags["flag_low"][i]),
        ))
    db.flush()

    # ---- 6. episodes ---------------------------------------------------------
    report(0.70, "clustering fire episodes")
    fire_arr = arr.subset(~arr.static_source)
    episodes = cluster_episodes(
        fire_arr, space_km=cfg.episode_space_km, time_days=cfg.episode_time_days,
    )
    episodes.sort(key=lambda e: e.intensity_index, reverse=True)
    critical_ids = {id(e) for e in episodes[:10]}
    episodes.sort(key=lambda e: (e.start_ord, -e.n_detections))
    for i, ep in enumerate(episodes, start=1):
        db.add(FireEpisode(
            aoi_id=aoi_id, label=f"{aoi_id}-EP{i:05d}",
            start_date=date.fromordinal(ep.start_ord),
            end_date=date.fromordinal(ep.end_ord),
            duration_days=ep.end_ord - ep.start_ord + 1,
            n_detections=ep.n_detections,
            peak_frp_mw=ep.peak_frp,
            total_frp_mw=ep.total_frp,
            intensity_index=ep.intensity_index,
            centroid_lat=ep.centroid_lat, centroid_lon=ep.centroid_lon,
            bounds_west=ep.bounds[0], bounds_south=ep.bounds[1],
            bounds_east=ep.bounds[2], bounds_north=ep.bounds[3],
            sensor_mix_json=json.dumps(ep.sensor_mix, sort_keys=True),
            is_critical=id(ep) in critical_ids,
        ))
    db.flush()

    # ---- 7. seasons (with censoring) -----------------------------------------
    report(0.88, "fire-season metrics")
    years = sorted({date.fromordinal(int(o)).year for o in date_ords})
    cards: list[SeasonCard] = season_metrics(date_ords, n_h, years)
    for card in cards:
        partial = _year_is_partial(card.year, int(date_ords.min()), int(date_ords.max()))
        db.add(SeasonMetrics(
            aoi_id=aoi_id, year=card.year,
            onset_date=date.fromordinal(card.onset_ord) if card.onset_ord else None,
            end_date=date.fromordinal(card.end_ord) if card.end_ord else None,
            season_length_days=card.season_length_days,
            peak_date=date.fromordinal(card.peak_ord) if card.peak_ord else None,
            peak_value=card.peak_value,
            active_days=card.active_days,
            max_streak_days=card.max_streak_days,
            is_partial=partial,
        ))
    db.flush()

    # ---- system state ---------------------------------------------------------
    state = db.get(SystemState, f"pipeline:{aoi_id}")
    payload = json.dumps({
        "design": "phase2_comparable_subset",
        "agreement_stat": round(agreement, 4),
        "counts_fit": transfer.as_params() if transfer else None,
        "frp_fit": frp_transfer.as_params() if frp_transfer else None,
        "comparison_power_law": (comparison_fit.as_dict()
                                 if comparison_fit else None),
        "continuity_n20": {k: v for k, v in cont_n20.items() if k != "per_year"},
        "continuity_n21": {k: v for k, v in cont_n21.items() if k != "per_year"},
        "pairing": pairing_stats.as_dict(),
        "episodes": len(episodes),
        "baseline_years": [cfg.baseline_start, cfg.baseline_end],
        "anomaly": {"method": "loyo_empirical_percentile",
                    "min_baseline_years": cfg.anomaly_min_baseline_years,
                    "pct_high": 95.0},
        "limitations": LIMITATIONS,
    }, sort_keys=True)
    if state is None:
        db.add(SystemState(key=f"pipeline:{aoi_id}", value_json=payload))
    else:
        state.value_json = payload

    db.commit()
    report(1.0, "done")
    return {
        "status": "OK",
        "agreement_stat": round(agreement, 4),
        "pairing": pairing_stats.as_dict(),
        "episodes": len(episodes),
    }


def _frp_monthly_pairs(arr, years: list[int]) -> dict:
    """FRP sums (comparable subset) in the same pair layout as counts."""
    is_day = arr.daynight == 0
    comp = is_day & ~arr.static_source
    frp = np.where(np.isfinite(arr.frp_mw), arr.frp_mw, 0.0)
    ord_month = np.array([date.fromordinal(int(o)).month for o in arr.date_ord], dtype=np.int64)
    ord_year = np.array([date.fromordinal(int(o)).year for o in arr.date_ord], dtype=np.int64)
    groups = {
        "pooled": comp & ((arr.satellite == 0) | (arr.satellite == 1)),
        "terra": comp & (arr.satellite == 0),
        "aqua": comp & (arr.satellite == 1),
    }
    snpp = comp & (arr.satellite == 2)
    out: dict[str, dict] = {}
    for name, mask in groups.items():
        per_year = {}
        for y in years:
            months = {}
            for m in range(1, 13):
                sel = (ord_year == y) & (ord_month == m)
                months[m] = (float(frp[mask & sel].sum()), float(frp[snpp & sel].sum()))
            per_year[y] = months
        out[name] = per_year
    return out


def _fit_power_law_comparison(pairs: dict) -> TransferFit | None:
    """Retired Phase 1 power-law on the comparable subset (Methods panel)."""
    years = sorted(pairs["pooled"])
    m_monthly: list[float] = []
    v_monthly: list[float] = []
    for y in years:
        for m in range(1, 13):
            mm, vv = pairs["pooled"][y][m]
            m_monthly.append(mm)
            v_monthly.append(vv)
    return fit_transfer(np.asarray(m_monthly), np.asarray(v_monthly), "COUNTS")


def _monthly_agreement(pooled_pairs: dict) -> float:
    """Spearman rho between comparable MODIS and SNPP monthly sums over the
    calibration window (scale-free lie detector for the fitted factors)."""
    from scipy.stats import spearmanr

    m_all: list[float] = []
    v_all: list[float] = []
    for y in sorted(pooled_pairs):
        for m in range(1, 13):
            mm, vv = pooled_pairs[y][m]
            if mm > 0 or vv > 0:
                m_all.append(mm)
                v_all.append(vv)
    if len(m_all) < 12:
        return 0.0
    rho = spearmanr(np.asarray(m_all), np.asarray(v_all)).statistic
    return float(rho) if np.isfinite(rho) else 0.0


def _year_is_partial(year: int, first_ord: int, last_ord: int) -> bool:
    """Season censoring: a year is partial when the harmonized record does
    not span its full calendar (edge years such as 2000 starting in Nov or
    the running year). Partial seasons are excluded from baseline stats."""
    jan1 = date(year, 1, 1).toordinal()
    dec31 = date(year, 12, 31).toordinal()
    return first_ord > jan1 or last_ord < dec31


def _r2(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.size < 2 or np.allclose(b, b[0]):
        return 0.0
    ss_res = float(np.sum((a - b) ** 2))
    ss_tot = float(np.sum((b - b.mean()) ** 2))
    return max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0
