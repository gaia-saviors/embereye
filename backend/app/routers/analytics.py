"""Analytics: summary cards, time series, headline, climatology, seasons, episodes, methods."""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..harmonize.climatology import _doy_slot
from ..harmonize.pipeline import CITATIONS, LIMITATIONS as LIMITATIONS_FALLBACK
from ..models import CalibrationModel, FireEpisode, HarmonizedDaily, Hotspot, SeasonMetrics, SystemState
from ..schemas import (
    BaselineInfo,
    CalibrationOut,
    ClimatologyDoy,
    ClimatologyResponse,
    EpisodeOut,
    HeadlineResponse,
    MethodsResponse,
    SeasonOut,
    SummaryResponse,
    TimeseriesPoint,
)

router = APIRouter()


def _rows(db: Session, aoi_id: str, start: date | None, end: date | None) -> list[HarmonizedDaily]:
    stmt = select(HarmonizedDaily).where(HarmonizedDaily.aoi_id == aoi_id).order_by(HarmonizedDaily.local_date)
    if start is not None:
        stmt = stmt.where(HarmonizedDaily.local_date >= start)
    if end is not None:
        stmt = stmt.where(HarmonizedDaily.local_date <= end)
    return list(db.execute(stmt).scalars().all())


def _season_or_none(s: SeasonMetrics) -> dict:
    # model_dump(): pass a plain dict to the response model. Function-level
    # imports under pytest's sys.modules purging can produce two generations
    # of the pydantic models; a dict validates against whichever generation
    # the route was bound to (see test_v106 note).
    return SeasonOut(
        year=s.year, onset_date=s.onset_date, end_date=s.end_date,
        season_length_days=s.season_length_days, peak_date=s.peak_date,
        peak_value=round(s.peak_value, 2), active_days=s.active_days,
        max_streak_days=s.max_streak_days, is_partial=s.is_partial,
    ).model_dump()


def _episode_out(e: FireEpisode) -> dict:
    return EpisodeOut(
        id=e.id, aoi_id=e.aoi_id, label=e.label, start_date=e.start_date,
        end_date=e.end_date, duration_days=e.duration_days,
        n_detections=e.n_detections, peak_frp_mw=round(e.peak_frp_mw, 2),
        total_frp_mw=round(e.total_frp_mw, 2),
        intensity_index=round(e.intensity_index, 1),
        centroid_lat=round(e.centroid_lat, 5), centroid_lon=round(e.centroid_lon, 5),
        bounds=[e.bounds_west, e.bounds_south, e.bounds_east, e.bounds_north],
        sensor_mix=json.loads(e.sensor_mix_json or "{}"), is_critical=e.is_critical,
    ).model_dump()


@router.get("/analytics/summary", response_model=SummaryResponse)
def summary(
    aoi: str = Query(...), start: date | None = None, end: date | None = None,
    include_layers: bool = Query(False, description="Add the static + night "
                        "layers (from 2012) into the headline totals"),
    db: Session = Depends(get_db),
) -> SummaryResponse:
    cfg = get_settings()
    rows = _rows(db, aoi, start, end)
    if not rows:
        raise HTTPException(404, "no data for AOI")
    n_h = np.array([r.n_harmonized for r in rows])
    lo = np.array([r.n_harmonized_lo if r.n_harmonized_lo is not None else r.n_harmonized
                   for r in rows])
    hi = np.array([r.n_harmonized_hi if r.n_harmonized_hi is not None else r.n_harmonized
                   for r in rows])
    frp_h = np.array([r.frp_harmonized_mw for r in rows])
    peak_idx = int(n_h.argmax())
    peak = rows[peak_idx]
    seasons = db.execute(
        select(SeasonMetrics).where(SeasonMetrics.aoi_id == aoi).order_by(SeasonMetrics.year)
    ).scalars().all()
    current = seasons[-1] if seasons else None
    baseline_lengths = [s.season_length_days for s in seasons
                        if s.season_length_days and not s.is_partial
                        and cfg.baseline_start <= s.year <= cfg.baseline_end]
    top_episode = db.execute(
        select(FireEpisode).where(FireEpisode.aoi_id == aoi)
        .order_by(FireEpisode.intensity_index.desc()).limit(1)
    ).scalar_one_or_none()
    prov_split: dict[str, int] = {}
    for r in rows:
        prov_split[r.provenance] = prov_split.get(r.provenance) or 0
        prov_split[r.provenance] += 1
    # Separate layers (reference platform, from 2012): burning activity per
    # the challenge text - shown always, added to totals only when asked.
    n_static_total = int(sum(r.n_static for r in rows))
    n_night_total = int(sum(r.n_night for r in rows))
    layer_denom = n_static_total + n_night_total + float(n_h.sum())
    static_share = (n_static_total / layer_denom) if layer_denom > 0 else None

    # Phase 3A (defect 10): the ONE displayed static-share metric is
    # measured on the VIIRS era only. Pre-2012 the static layer cannot be
    # measured (no VIIRS), so the old denominator mixed modelled transferred
    # comparable counts with an unmeasurable numerator part. From 2012-01-20
    # all three denominator components are observed.
    viirs_start = date(2012, 1, 20)  # first VIIRS-native day (see meta.py eras)
    era_rows = [r for r in rows if r.local_date >= viirs_start]
    era_static = int(sum(r.n_static for r in era_rows))
    era_night = int(sum(r.n_night for r in era_rows))
    era_comparable = float(sum(r.n_harmonized for r in era_rows))
    era_denom = era_static + era_night + era_comparable
    static_share_era = (era_static / era_denom) if era_denom > 0 else None
    static_share_def = (
        "Share of all burning activity measured since "
        f"{viirs_start.isoformat()} (the VIIRS era) that comes from permanent "
        "heat sources: static-source detections divided by (static + "
        "non-static night + comparable daytime non-static detections), "
        "S-NPP reference platform. The static layer does not exist before "
        f"{viirs_start.isoformat()}, so the metric covers the VIIRS era "
        "only - pre-2012 comparable detections are excluded from the "
        "denominator on purpose."
    )
    layers_add = (n_static_total + n_night_total) if include_layers else 0.0
    return SummaryResponse(
        aoi_id=aoi, start=rows[0].local_date, end=rows[-1].local_date,
        total_detections_harmonized=round(float(n_h.sum()) + layers_add, 1),
        total_detections_harmonized_lo=round(float(lo.sum()) + layers_add, 1),
        total_detections_harmonized_hi=round(float(hi.sum()) + layers_add, 1),
        total_frp_harmonized_mw=round(float(frp_h.sum()), 1),
        detections_modis=int(sum(r.n_modis for r in rows)),
        detections_viirs=int(sum(r.n_viirs for r in rows)),
        n_static_total=n_static_total,
        n_night_total=n_night_total,
        static_share=None if static_share_era is None else round(static_share_era, 4),
        static_share_numerator=era_static,
        static_share_denominator=int(round(era_denom)),
        static_share_definition=static_share_def,
        static_share_period_start=viirs_start,
        static_share_period_end=rows[-1].local_date,
        # Phase 3A (defect 5): per-era season-length medians; pre-2012
        # seasons are computed from transferred (estimated) daily data and
        # the two eras are never averaged into one number.
        season_median_transferred_era=(
            round(float(np.median([
                s.season_length_days for s in seasons
                if s.season_length_days and not s.is_partial
                and s.year < cfg.baseline_start
            ])), 1)
            if any(s.season_length_days and not s.is_partial and s.year < cfg.baseline_start
                   for s in seasons) else None
        ),
        season_median_viirs_era=(
            round(float(np.median([
                s.season_length_days for s in seasons
                if s.season_length_days and not s.is_partial
                and s.year >= cfg.baseline_start
            ])), 1)
            if any(s.season_length_days and not s.is_partial and s.year >= cfg.baseline_start
                   for s in seasons) else None
        ),
        season_length_definition=(
            "Days in the shortest window around the year's 7-day-smoothed "
            "peak that contains 80% of that year's harmonized detections "
            "(80%-of-activity definition; replaces the old 10%-of-peak "
            "rule). Years before 2012 sit on transferred (estimated) "
            "daily data and are shown separately - the two eras are not "
            "directly comparable."
        ),
        frp_modis_mw=round(float(sum(r.frp_modis_mw for r in rows)), 1),
        frp_viirs_mw=round(float(sum(r.frp_viirs_mw for r in rows)), 1),
        active_days=int((n_h > 0).sum()),
        peak_day=None if (peak.n_harmonized <= 0 and peak.frp_harmonized_mw <= 0) else _cell(peak),
        n_anomaly_high=int(sum(1 for r in rows if r.flag_high)),
        n_anomaly_low=int(sum(1 for r in rows if r.flag_low)),
        season_current=_season_or_none(current) if current else None,
        season_baseline_mean_length=(
            round(float(np.mean(baseline_lengths)), 1) if baseline_lengths else None
        ),
        top_episode=_episode_out(top_episode) if top_episode else None,
        provenance_split=prov_split,
    )


def _cell(r: HarmonizedDaily) -> dict:
    # model_dump(): see _season_or_none note (pytest module-purge safety).
    from ..schemas import CalendarCell

    return CalendarCell(
        date=r.local_date, year=r.local_date.year, doy=r.local_date.timetuple().tm_yday,
        n_modis=r.n_modis, n_viirs=r.n_viirs, n_harmonized=round(r.n_harmonized, 2),
        n_harmonized_lo=None if r.n_harmonized_lo is None else round(r.n_harmonized_lo, 2),
        n_harmonized_hi=None if r.n_harmonized_hi is None else round(r.n_harmonized_hi, 2),
        n_comparable_modis=r.n_comparable_modis, n_comparable_viirs=r.n_comparable_viirs,
        n_static=r.n_static, n_night=r.n_night,
        static_share=None if r.static_share is None else round(r.static_share, 4),
        frp_modis_mw=round(r.frp_modis_mw, 2), frp_viirs_mw=round(r.frp_viirs_mw, 2),
        frp_harmonized_mw=round(r.frp_harmonized_mw, 2),
        z_n=None if r.z_n is None else round(r.z_n, 3),
        z_frp=None if r.z_frp is None else round(r.z_frp, 3),
        pct_n=None if r.pct_n is None else round(r.pct_n, 2),
        flag_high=r.flag_high, flag_low=r.flag_low,
        seam_weight=round(r.seam_weight, 4), provenance=r.provenance,
    ).model_dump(mode="json")


def _moy_counts_params(db: Session, aoi_id: str) -> dict | None:
    """Stored COUNTS moy_factor_v1 params (factors + LOYO reports), if the
    AOI has been rebuilt with pipeline >= v1.0.6."""
    fit = db.execute(
        select(CalibrationModel).where(
            CalibrationModel.aoi_id == aoi_id,
            CalibrationModel.variable == "COUNTS",
            CalibrationModel.method == "moy_factor_v1",
        )
    ).scalar_one_or_none()
    if fit is None:
        return None
    try:
        return json.loads(fit.params_json or "{}")
    except ValueError:
        return None


def _res_interval(params: dict | None, res: str) -> tuple[float, float] | None:
    """Aggregation-matched LOYO interval factor (1+q2.5, 1+q97.5).

    Falls back to the annual PI when the stored fit predates v1.0.6 (no
    monthly/weekly report) - the caller labels which basis was used."""
    if not params:
        return None
    if res == "month" and params.get("n_monthly_errors"):
        return float(params["pi_monthly_low"]), float(params["pi_monthly_high"])
    if res == "week" and params.get("n_weekly_errors"):
        return float(params["pi_weekly_low"]), float(params["pi_weekly_high"])
    if params.get("pi_low") is not None and params.get("pi_high") is not None:
        return float(params["pi_low"]), float(params["pi_high"])
    return None


@router.get("/analytics/timeseries", response_model=list[TimeseriesPoint])
def timeseries(
    aoi: str = Query(...), start: date | None = None, end: date | None = None,
    res: str = Query("day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
) -> list[TimeseriesPoint]:
    rows = _rows(db, aoi, start, end)
    if not rows:
        raise HTTPException(404, "no data for AOI")

    def lo_of(r: HarmonizedDaily) -> float:
        return r.n_harmonized_lo if r.n_harmonized_lo is not None else r.n_harmonized

    def hi_of(r: HarmonizedDaily) -> float:
        return r.n_harmonized_hi if r.n_harmonized_hi is not None else r.n_harmonized

    if res == "day":
        return [
            TimeseriesPoint(
                date=r.local_date, n_modis=r.n_modis, n_viirs=r.n_viirs,
                n_harmonized=round(r.n_harmonized, 2),
                n_harmonized_lo=round(lo_of(r), 2),
                n_harmonized_hi=round(hi_of(r), 2),
                n_static=r.n_static, n_night=r.n_night,
                frp_modis_mw=round(r.frp_modis_mw, 2),
                frp_viirs_mw=round(r.frp_viirs_mw, 2),
                frp_harmonized_mw=round(r.frp_harmonized_mw, 2),
                z_n=None if r.z_n is None else round(r.z_n, 3),
                interval_basis="annual-LOYO" if r.provenance in ("MODIS_TRANSFERRED", "MODIS_ONLY") else None,
            )
            for r in rows
        ]
    buckets: dict[tuple, list[HarmonizedDaily]] = {}
    for r in rows:
        if res == "week":
            iso = r.local_date.isocalendar()
            key = (iso[0], iso[1])
        else:
            key = (r.local_date.year, r.local_date.month)
        buckets.setdefault(key, []).append(r)
    # Aggregation-matched interval (v1.0.6): the band at week/month res is
    # the LOYO error distribution AT that aggregation, applied to the
    # transferred (MODIS-era) share of the bucket; native-era days carry no
    # transfer uncertainty and keep lo=hi=value.
    params = _moy_counts_params(db, aoi)
    pi = _res_interval(params, res)
    basis = None
    if pi is not None:
        basis = {"month": "monthly-LOYO", "week": "weekly-LOYO"}.get(res, "annual-LOYO")
        if not (params or {}).get("n_monthly_errors"):
            basis = "annual-LOYO (rebuild for aggregation-matched band)"
    out: list[TimeseriesPoint] = []
    for key in sorted(buckets):
        grp = buckets[key]
        y, w = key
        anchor = grp[0].local_date
        if res == "week":
            anchor = date.fromisocalendar(y, w, 1)
        else:
            anchor = date(y, w, 1)
        total_h = float(sum(r.n_harmonized for r in grp))
        transferred_h = float(sum(r.n_harmonized for r in grp
                                  if r.provenance in ("MODIS_TRANSFERRED", "MODIS_ONLY")))
        if pi is not None and transferred_h > 0:
            native_h = total_h - transferred_h
            lo_val = transferred_h * (1.0 + pi[0]) + native_h
            hi_val = transferred_h * (1.0 + pi[1]) + native_h
        else:
            lo_val = float(sum(lo_of(r) for r in grp))
            hi_val = float(sum(hi_of(r) for r in grp))
        total_frp = float(sum(r.frp_harmonized_mw for r in grp))
        out.append(TimeseriesPoint(
            date=anchor,
            n_modis=int(sum(r.n_modis for r in grp)),
            n_viirs=int(sum(r.n_viirs for r in grp)),
            n_harmonized=round(total_h, 1),
            n_harmonized_lo=round(lo_val, 1),
            n_harmonized_hi=round(hi_val, 1),
            n_static=int(sum(r.n_static for r in grp)),
            n_night=int(sum(r.n_night for r in grp)),
            frp_modis_mw=round(float(sum(r.frp_modis_mw for r in grp)), 1),
            frp_viirs_mw=round(float(sum(r.frp_viirs_mw for r in grp)), 1),
            frp_harmonized_mw=round(total_frp, 1),
            z_n=None,
            interval_basis=basis,
        ))
    return out


@router.get("/analytics/climatology", response_model=ClimatologyResponse)
def climatology(
    aoi: str = Query(...), db: Session = Depends(get_db)
) -> ClimatologyResponse:
    cfg = get_settings()
    rows = [
        r for r in _rows(db, aoi, None, None)
        if cfg.baseline_start <= r.local_date.year <= cfg.baseline_end
    ]
    if not rows:
        raise HTTPException(404, "no baseline-window data for AOI")
    by_slot: dict[int, list[HarmonizedDaily]] = {}
    for r in rows:
        by_slot.setdefault(_doy_slot(r.local_date), []).append(r)
    doy_out: list[ClimatologyDoy] = []
    for slot in range(1, 367):
        grp = by_slot.get(slot, [])
        if not grp:
            continue
        vals = np.array([r.n_harmonized for r in grp])
        frps = np.array([r.frp_harmonized_mw for r in grp])
        doy_out.append(ClimatologyDoy(
            doy=slot, mean_n=round(float(vals.mean()), 3),
            p10_n=round(float(np.percentile(vals, 10)), 3),
            p90_n=round(float(np.percentile(vals, 90)), 3),
            mean_frp=round(float(frps.mean()), 2),
        ))
    return ClimatologyResponse(
        aoi_id=aoi,
        baseline=BaselineInfo(
            start_year=cfg.baseline_start, end_year=cfg.baseline_end,
            kernel_days=15, z_threshold=cfg.anomaly_z_threshold,
            definition="Empirical DOY distribution over the baseline window (computed from stored harmonized rows).",
        ),
        doy=doy_out,
    )


# A verdict needs at least this many days of the month in the record;
# below it the comparison would pit a 1-2 day sliver against history
# (the 0.0-vs-79.0 artifact reported in the 2026-11-02 handoff).
HEADLINE_MIN_DAYS = 5


@router.get("/analytics/headline", response_model=HeadlineResponse)
def headline(aoi: str = Query(...), db: Session = Depends(get_db)) -> HeadlineResponse:
    """Headline analytic (challenge): current month TO DATE vs the
    historical median of the SAME day-of-month window, comparable-subset
    series.

    v1.0.6 (handoff P2d): the old version compared a 1-2 day partial month
    against a FULL-month median (0.0 vs 79.0). Now every historical year
    contributes the sum of days 1..N of the same calendar month (N = days
    into the current month), so all samples span identical window lengths.
    Below HEADLINE_MIN_DAYS days the verdict wording is suppressed."""
    rows = _rows(db, aoi, None, None)
    if not rows:
        raise HTTPException(404, "no data for AOI")
    last = rows[-1].local_date
    cur_month = date(last.year, last.month, 1)
    month_end = _month_end(cur_month)
    current_partial = last < month_end
    days_in_month = last.day
    current_total = float(sum(r.n_harmonized for r in rows
                              if r.local_date >= cur_month))
    # Historical same-window totals: days 1..days_in_month of the same
    # calendar month, only for years the record fully covers through that
    # day (never the running year, never censored starts).
    hist: list[tuple[int, float]] = []
    for r in rows:
        d = r.local_date
        if (d.month == cur_month.month and d.year < cur_month.year
                and d.day <= days_in_month):
            hist.append((d.year, 0.0))  # placeholder, summed below
    hist_vals: dict[int, float] = {y: 0.0 for y, _ in hist}
    for r in rows:
        d = r.local_date
        if (d.month == cur_month.month and d.year < cur_month.year
                and d.year in hist_vals and d.day <= days_in_month):
            hist_vals[d.year] += r.n_harmonized
    # A historical year qualifies only if its record covers the FULL window
    # (day 1 through days_in_month); otherwise its sample is censored.
    covered_years = set()
    for r in rows:
        d = r.local_date
        if (d.year < cur_month.year and d.month == cur_month.month
                and d.year in hist_vals and d.day == 1):
            covered_years.add(d.year)
    covered_years = {
        y for y in covered_years
        if any(r.local_date.year == y and r.local_date.month == cur_month.month
               and r.local_date.day == days_in_month for r in rows)
    }
    hist = sorted((y, v) for y, v in hist_vals.items() if y in covered_years)
    too_few_days = days_in_month < HEADLINE_MIN_DAYS
    if hist and not too_few_days:
        vals = np.asarray([v for _, v in hist], dtype=np.float64)
        median = float(np.median(vals))
        p25, p75 = (float(np.percentile(vals, 25)), float(np.percentile(vals, 75)))
        rank = 100.0 * float(np.count_nonzero(vals <= current_total)) / vals.size
        if current_total > p75:
            verdict = "above the historical range (p75)"
        elif current_total < p25:
            verdict = "below the historical range (p25)"
        else:
            verdict = "within the historical interquartile range"
    else:
        median = p25 = p75 = None
        rank = None
        if too_few_days:
            verdict = (f"no verdict: only {days_in_month} day(s) into the "
                       f"month (minimum {HEADLINE_MIN_DAYS})")
        else:
            verdict = "no comparable historical month yet"
    note = (
        f"Month-to-date through day {days_in_month} of the month, compared "
        f"with days 1-{days_in_month} of the same month in "
        f"{len(hist)} historical year(s)."
        if hist else
        f"Month is {days_in_month} day(s) old; no comparable historical "
        "window yet."
    )
    if current_partial and days_in_month < HEADLINE_MIN_DAYS:
        note += " Verdict suppressed: window too short to be meaningful."

    # ---- Phase 3A (defect 6): the PRIMARY headline = last COMPLETE month ---
    # A 1-2-day month shows nothing; fall back to the most recent month the
    # record covers end-to-end, compared against FULL-month totals of the
    # same calendar month in history (only years whose record covers that
    # entire month; never the running year).
    complete_month = None
    if current_partial:
        prev_first = (cur_month - timedelta(days=1)).replace(day=1)
        complete_month = prev_first
    else:
        complete_month = cur_month
    comp_end = _month_end(complete_month)
    comp_total = float(sum(r.n_harmonized for r in rows
                           if complete_month <= r.local_date <= comp_end))
    # Historical FULL-month totals for the same calendar month.
    comp_hist: dict[int, float] = {}
    covers_month: set[int] = set()
    for r in rows:
        d = r.local_date
        if (d.month == complete_month.month and d.year < complete_month.year
                and d.year not in comp_hist):
            comp_hist[d.year] = 0.0
    for r in rows:
        d = r.local_date
        if (d.month == complete_month.month and d.year < complete_month.year
                and d.year in comp_hist):
            comp_hist[d.year] += r.n_harmonized
            if d.day == _month_end(date(d.year, d.month, 1)).day:
                covers_month.add(d.year)
    comp_hist = {y: v for y, v in comp_hist.items() if y in covers_month}
    comp_vals = np.asarray(sorted(comp_hist.values()), dtype=np.float64)
    if comp_vals.size and not np.allclose(comp_vals, 0.0):
        c_median = float(np.median(comp_vals))
        c_p25, c_p75 = (float(np.percentile(comp_vals, 25)),
                        float(np.percentile(comp_vals, 75)))
        c_rank = 100.0 * float(np.count_nonzero(comp_vals <= comp_total)) / comp_vals.size
        if comp_total > c_p75:
            c_verdict = "above the historical range (p75)"
        elif comp_total < c_p25:
            c_verdict = "below the historical range (p25)"
        else:
            c_verdict = "within the historical interquartile range"
        comp_note = (
            f"{complete_month.strftime('%B %Y')} (the last month the record "
            f"covers end-to-end) vs full-month totals of "
            f"{complete_month.strftime('%B')} in {comp_vals.size} historical "
            "year(s). This is the primary figure while the current month is "
            "still in progress."
        )
    else:
        c_median = c_p25 = c_p75 = c_rank = None
        c_verdict = "no comparable historical month yet"
        comp_note = (
            f"{complete_month.strftime('%B %Y')} is the last complete month, "
            "but the record has no fully covered "
            f"{complete_month.strftime('%B')} from an earlier year yet."
        )

    # ---- STEP B (pending fix): "provisional" NRT labelling + Oct/NRT ------
    # diagnostics. A month whose detections include NRT product rows is
    # still revisable upstream (Methods > SP/NRT seam), so the headline
    # labels it provisional and quantifies the NRT share of BOTH the
    # in-progress month and the primary complete month. This is what makes
    # a weak October (e.g. Oct 2026: 159 vs a median of 1,081) legible as
    # a provisional tail month rather than a silent collapse.
    def _nrt_share(month_first: date, month_last: date) -> float | None:
        tot = db.execute(
            select(func.count()).select_from(Hotspot)
            .where(Hotspot.aoi_id == aoi)
            .where(Hotspot.local_date >= month_first)
            .where(Hotspot.local_date <= month_last)
        ).scalar()
        if not tot:
            return None
        nrt = db.execute(
            select(func.count()).select_from(Hotspot)
            .where(Hotspot.aoi_id == aoi)
            .where(Hotspot.local_date >= month_first)
            .where(Hotspot.local_date <= month_last)
            .where(Hotspot.firms_product == "NRT")
        ).scalar()
        return round(100.0 * (nrt or 0) / tot, 1)

    nrt_share_month = _nrt_share(cur_month, month_end)
    nrt_share_complete = _nrt_share(complete_month, _month_end(complete_month))
    provisional = bool((nrt_share_month or 0) > 0 or (nrt_share_complete or 0) > 0)
    provisional_note = ""
    if provisional:
        parts = []
        if nrt_share_complete is not None and nrt_share_complete > 0:
            parts.append(
                f"{complete_month.strftime('%B %Y')}: {nrt_share_complete}% of "
                "rows are NRT (near-real-time, still revisable upstream)"
            )
        if current_partial and nrt_share_month is not None and nrt_share_month > 0:
            parts.append(
                f"{cur_month.strftime('%B %Y')}: {nrt_share_month}% NRT"
            )
        provisional_note = (
            "Provisional - the tail of the record is NRT product data. "
            + "; ".join(parts) + ". See Methods > SP/NRT seam."
        )

    return HeadlineResponse(
        aoi_id=aoi, month=cur_month, current_total=round(current_total, 1),
        current_partial=current_partial, days_in_month=days_in_month,
        historical_median=None if median is None else round(median, 1),
        historical_p25=None if p25 is None else round(p25, 1),
        historical_p75=None if p75 is None else round(p75, 1),
        historical_years=[y for y, _ in hist],
        percentile_rank=None if rank is None else round(rank, 1),
        verdict=verdict, note=note,
        min_days_for_verdict=HEADLINE_MIN_DAYS,
        complete_month=complete_month,
        complete_total=round(comp_total, 1),
        complete_median=None if c_median is None else round(c_median, 1),
        complete_p25=None if c_p25 is None else round(c_p25, 1),
        complete_p75=None if c_p75 is None else round(c_p75, 1),
        complete_years=sorted(comp_hist.keys()),
        complete_percentile_rank=None if c_rank is None else round(c_rank, 1),
        complete_verdict=c_verdict,
        complete_note=comp_note,
        provisional=provisional,
        nrt_share_month=nrt_share_month,
        nrt_share_complete=nrt_share_complete,
        provisional_note=provisional_note,
    )


def _month_end(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


@router.get("/analytics/seasons", response_model=list[SeasonOut])
def seasons(aoi: str = Query(...), db: Session = Depends(get_db)) -> list[SeasonOut]:
    rows = db.execute(
        select(SeasonMetrics).where(SeasonMetrics.aoi_id == aoi).order_by(SeasonMetrics.year)
    ).scalars().all()
    if not rows:
        raise HTTPException(404, "no season metrics for AOI")
    return [_season_or_none(s) for s in rows]


@router.get("/analytics/episodes", response_model=list[EpisodeOut])
def episodes(
    aoi: str = Query(...), start: date | None = None, end: date | None = None,
    only_critical: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[EpisodeOut]:
    stmt = select(FireEpisode).where(FireEpisode.aoi_id == aoi)
    if start is not None:
        stmt = stmt.where(FireEpisode.end_date >= start)
    if end is not None:
        stmt = stmt.where(FireEpisode.start_date <= end)
    if only_critical:
        stmt = stmt.where(FireEpisode.is_critical.is_(True))
    stmt = stmt.order_by(FireEpisode.intensity_index.desc()).limit(limit)
    rows = db.execute(stmt).scalars().all()
    out = [_episode_out(e) for e in rows]
    # _episode_out returns plain dicts (pydantic re-validation safety).
    return sorted(out, key=lambda e: (e["start_date"], -e["n_detections"]))


@router.get("/analytics/methods", response_model=MethodsResponse)
def methods(aoi: str = Query(...), db: Session = Depends(get_db)) -> MethodsResponse:
    cfg = get_settings()
    fits = db.execute(
        select(CalibrationModel).where(CalibrationModel.aoi_id == aoi)
        .order_by(CalibrationModel.variable)
    ).scalars().all()
    state = db.get(SystemState, f"pipeline:{aoi}")
    pipeline_meta: dict = {}
    if state is not None:
        try:
            pipeline_meta = json.loads(state.value_json)
        except ValueError:
            pipeline_meta = {}
    return MethodsResponse(
        aoi_id=aoi,
        calibrations=[
            CalibrationOut(
                variable=f.variable, direction=f.direction, method=f.method,
                slope=round(f.slope, 6), intercept=round(f.intercept, 6),
                r2_in_sample=round(f.r2_in_sample, 4), r2_oos=round(f.r2_oos, 4),
                r2_raw_oos=round(f.r2_raw_oos, 4),
                n_months=f.n_months, n_pairs=f.n_pairs,
                window_start=f.window_start, window_end=f.window_end,
                agreement_stat=round(f.agreement_stat, 4),
                params=json.loads(f.params_json or "{}"),
            )
            for f in fits
        ],
        pairing_stats=pipeline_meta.get("pairing", {}),
        pipeline={
            "design": pipeline_meta.get("design", "phase2_comparable_subset"),
            "baseline_years": [cfg.baseline_start, cfg.baseline_end],
            "anomaly": pipeline_meta.get("anomaly", {
                "method": "loyo_empirical_percentile",
                "min_baseline_years": cfg.anomaly_min_baseline_years,
                "pct_high": 95.0,
            }),
            "calibration_window": [cfg.calibration_start, cfg.calibration_end],
            "reference_platform": "SNPP (NOAA-20 after 2026-11-02, continuity-divided)",
            "seed_version": cfg.seed_version,
            "episodes": pipeline_meta.get("episodes"),
            "notes": (
                "Phase 2: harmonization targets the COMPARABLE SUBSET "
                "(daytime, non-static). Static + night layers are separate "
                "from 2012 and included in totals only when switched on. "
                "The monthly power-law is retired to a comparison column."
            ),
        },
        # Code is the source of truth for the limitations text (P3): the
        # list ships with the app and updates without any DB write. The
        # rebuild-time snapshot is kept alongside as history.
        limitations=LIMITATIONS_FALLBACK,
        limitations_at_rebuild=pipeline_meta.get("limitations"),
        citations=CITATIONS,
    )

