"""Burning-activity calendar endpoint: the challenge's core artifact."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import HarmonizedDaily, Hotspot, SeasonMetrics
from ..schemas import (
    BaselineInfo,
    CalendarCell,
    CalendarFinding,
    CalendarResponse,
    CalendarSummary,
    SeasonOut,
    TransferredWeek,
    WeeklyCell,
)

router = APIRouter()

# Provenance values that mark the MODIS-transferred era (incl. legacy DBs).
TRANSFERRED_PROVENANCE = {"MODIS_TRANSFERRED", "MODIS_ONLY"}
NATIVE_PROVENANCE = {"VIIRS_NATIVE", "VIIRS_FORWARD", "OVERLAP_FUSED"}
# Ring width for the weekly LOYO baseline: +/- 2 ISO weeks (week-of-year
# wraps through the 53-week ring, matching the daily +/-15-day kernel).
WEEK_RING = 2
ISO_WEEKS_MAX = 53


def _weekly_loyo(weeks: list[WeeklyCell]) -> None:
    """Week-level LOYO anomaly flags, applied IN-ERA (same guards as the
    daily path: +/-2-week ring, min 5 distinct baseline years, p95 high,
    zero-vs-median low). Mutates `weeks` in place. This is exactly the
    Phase 3A transferred-era test, now reused for the native era too."""
    n = len(weeks)
    vals = [w.n_harmonized for w in weeks]
    for i in range(n):
        wi = weeks[i].iso_week
        own_year = weeks[i].year
        sample: list[float] = []
        sample_years: set[int] = set()
        for j in range(n):
            if weeks[j].year == own_year:
                continue
            wj = weeks[j].iso_week
            ring = abs(wi - wj)
            ring = min(ring, ISO_WEEKS_MAX - ring)
            if ring <= WEEK_RING:
                sample.append(vals[j])
                sample_years.add(weeks[j].year)
        if len(sample_years) < 5 or not sample:
            continue  # min-count guard, same policy as the daily flags
        s = sorted(sample)
        p = 100.0 * sum(1 for v in s if v <= vals[i]) / len(s)
        weeks[i].pct_n = round(p, 2)
        if vals[i] > 0 and p >= 95.0:
            weeks[i].flag_high = True
        if vals[i] == 0 and float(s[len(s) // 2]) > 1.0:
            weeks[i].flag_low = True


def _weekly(rows: list[HarmonizedDaily], nrt_dates: set) -> list[WeeklyCell]:
    """STEP B: weekly cells for the WHOLE record - ONE scale, ONE unit.

    Both eras are aggregated to ISO weeks with the SAME code path; the
    anomaly test runs at the aggregation shown, inside each era separately
    (a transferred-era week never competes with a VIIRS-era week). The
    transferred-era values are identical to the Phase 3A transferred_weeks
    output; the native-era weeks are sums of the stored daily harmonized
    series. Weeks containing NRT product rows are marked provisional."""
    buckets: dict[tuple[int, int], dict] = {}
    for r in rows:
        iso = r.local_date.isocalendar()
        key = (iso[0], iso[1])
        b = buckets.setdefault(key, {
            "n": 0.0, "lo": 0.0, "hi": 0.0, "days": 0,
            "has_lohi": False, "prov": [],
        })
        b["n"] += float(r.n_harmonized)
        if r.n_harmonized_lo is not None and r.n_harmonized_hi is not None:
            b["lo"] += float(r.n_harmonized_lo)
            b["hi"] += float(r.n_harmonized_hi)
            b["has_lohi"] = True
        b["days"] += 1
        b["prov"].append(r.provenance)

    weeks: list[WeeklyCell] = []
    for (y, w), b in sorted(buckets.items()):
        start = date.fromisocalendar(y, w, 1)
        end = date.fromisocalendar(y, w, 7)
        transferred = (start < date(2012, 1, 20)) or any(p in TRANSFERRED_PROVENANCE for p in b["prov"])
        # A week containing ANY transferred-era day is drawn as estimated
        # (hatch + reduced saturation) - the conservative label: a week
        # that mixes eras is partly estimated, never fully measured.
        provenance = ("MODIS_TRANSFERRED" if transferred
                      else "VIIRS_NATIVE")
        provisional = any(d in nrt_dates for d in (start, end)) or any(
            start <= d <= end for d in nrt_dates)
        weeks.append(WeeklyCell(
            year=y, iso_week=w, start_date=start, end_date=end,
            n_harmonized=round(b["n"], 2),
            n_harmonized_lo=round(b["lo"], 2) if b["has_lohi"] else None,
            n_harmonized_hi=round(b["hi"], 2) if b["has_lohi"] else None,
            pct_n=None, flag_high=False, flag_low=False,
            n_days_with_data=b["days"],
            provenance=provenance, provisional=provisional,
        ))

    for era in (TRANSFERRED_PROVENANCE, NATIVE_PROVENANCE):
        _weekly_loyo([w for w in weeks if w.provenance in era])
    return weeks


def _nrt_dates(db: Session) -> set:
    """Local dates holding NRT product rows (near-real-time, revisable)."""
    return set(db.execute(
        select(Hotspot.local_date).where(Hotspot.firms_product == "NRT")
    ).scalars().all())


def _finding(rows: list[HarmonizedDaily], weeks: list[WeeklyCell]) -> CalendarFinding:
    """The headline finding, computed from the same weekly series the
    grid draws (single source of truth - the title cannot drift).

    Sep-Oct share: weeks are assigned to the month of their Wednesday
    (midpoint), which keeps 4-5 whole weeks per month regardless of how
    ISO weeks straddle month edges. The 80% window: pooled week-of-year
    profile, grown outward from the peak week until 80% of all activity
    is covered - the weekly analogue of the 80% season definition."""
    def mid_month(w) -> int:
        return date.fromisocalendar(w.year, w.iso_week, 4).month

    total = sum(w.n_harmonized for w in weeks)
    total = total if total > 0 else 1.0
    sep_oct = sum(w.n_harmonized for w in weeks if mid_month(w) in (9, 10))
    t_era = [w for w in weeks if w.provenance in TRANSFERRED_PROVENANCE]
    v_era = [w for w in weeks if w.provenance in NATIVE_PROVENANCE]
    t_tot = sum(w.n_harmonized for w in t_era)
    v_tot = sum(w.n_harmonized for w in v_era)
    sep_oct_t = (sum(w.n_harmonized for w in t_era if mid_month(w) in (9, 10)) / t_tot
                 if t_tot > 0 else None)
    sep_oct_v = (sum(w.n_harmonized for w in v_era if mid_month(w) in (9, 10)) / v_tot
                 if v_tot > 0 else None)

    # 80% window over the pooled week-of-year profile
    profile: dict[int, float] = {}
    for w in weeks:
        profile[w.iso_week] = profile.get(w.iso_week, 0.0) + w.n_harmonized
    total_p = sum(profile.values()) or 1.0
    peak_week = max(profile, key=lambda k: profile[k])
    order = sorted(profile, key=lambda k: (min(abs(k - peak_week),
                                               ISO_WEEKS_MAX - abs(k - peak_week)), k))
    acc = 0.0
    in_window: set[int] = set()
    for k in order:
        in_window.add(k)
        acc += profile[k]
        if acc >= 0.8 * total_p:
            break
    w80_start = min(in_window)
    w80_end = max(in_window)
    span = abs(w80_end - w80_start) + 1
    if w80_end < w80_start:  # wrapped through week 53
        span = (ISO_WEEKS_MAX - w80_start) + w80_end

    # 2018 daytime drop (AUDIT_REAL A6: real, cause open). Daytime = the
    # non-night part of the harmonized series (night layer is additive).
    day = {r.local_date: max(0.0, float(r.n_harmonized) - float(r.n_night or 0.0))
           for r in rows}
    y2017 = sum(v for d, v in day.items() if d.year == 2017)
    y2018 = sum(v for d, v in day.items() if d.year == 2018)
    y2018_change = (round(100.0 * (y2018 - y2017) / y2017, 1)
                    if y2017 > 0 else None)

    return CalendarFinding(
        annual_total=round(total, 1),
        sep_oct_share=round(100.0 * sep_oct / total, 1),
        sep_oct_share_transferred=None if sep_oct_t is None else round(100.0 * sep_oct_t, 1),
        sep_oct_share_native=None if sep_oct_v is None else round(100.0 * sep_oct_v, 1),
        w80_start_week=w80_start, w80_end_week=w80_end, w80_weeks_span=span,
        y2018_daytime_change_pct=y2018_change,
    )


@router.get("/calendar", response_model=CalendarResponse)
def calendar(
    aoi: str = Query(..., description="AOI id"),
    start: date | None = None,
    end: date | None = None,
    start_year: int | None = Query(default=None, description="Filter start year (inclusive)"),
    end_year: int | None = Query(default=None, description="Filter end year (inclusive)"),
    compact: bool = Query(default=False, description="Exclude verbose diagnostic fields"),
    db: Session = Depends(get_db),
) -> CalendarResponse:
    cfg = get_settings()
    stmt = select(HarmonizedDaily).where(HarmonizedDaily.aoi_id == aoi).order_by(HarmonizedDaily.local_date)
    if start_year is not None and isinstance(start_year, int):
        stmt = stmt.where(HarmonizedDaily.local_date >= date(start_year, 1, 1))
    elif start is not None:
        stmt = stmt.where(HarmonizedDaily.local_date >= start)
    if end_year is not None and isinstance(end_year, int):
        stmt = stmt.where(HarmonizedDaily.local_date <= date(end_year, 12, 31))
    elif end is not None:
        stmt = stmt.where(HarmonizedDaily.local_date <= end)
    rows = db.execute(stmt).scalars().all()
    if not rows:
        raise HTTPException(404, "no calendar data for AOI; run the aggregation pipeline")

    cells = [
        CalendarCell(
            date=r.local_date, year=r.local_date.year,
            doy=r.local_date.timetuple().tm_yday,
            n_modis=r.n_modis, n_viirs=r.n_viirs,
            n_harmonized=round(r.n_harmonized, 1),
            n_harmonized_lo=None if r.n_harmonized_lo is None or compact else round(r.n_harmonized_lo, 1),
            n_harmonized_hi=None if r.n_harmonized_hi is None or compact else round(r.n_harmonized_hi, 1),
            n_comparable_modis=r.n_comparable_modis if not compact else 0,
            n_comparable_viirs=r.n_comparable_viirs if not compact else 0,
            n_static=r.n_static, n_night=r.n_night,
            static_share=None if r.static_share is None or compact else round(r.static_share, 4),
            frp_modis_mw=round(r.frp_modis_mw, 1),
            frp_viirs_mw=round(r.frp_viirs_mw, 1),
            frp_harmonized_mw=round(r.frp_harmonized_mw, 1),
            z_n=None if compact else (None if r.z_n is None else round(r.z_n, 2)),
            z_frp=None if compact else (None if r.z_frp is None else round(r.z_frp, 2)),
            pct_n=None if r.pct_n is None else round(r.pct_n, 1),
            flag_high=r.flag_high, flag_low=r.flag_low,
            seam_weight=round(r.seam_weight, 2), provenance=r.provenance,
        )
        for r in rows
    ]
    seasons = db.execute(
        select(SeasonMetrics).where(SeasonMetrics.aoi_id == aoi).order_by(SeasonMetrics.year)
    ).scalars().all()
    years = sorted({c.date.year for c in cells})
    # STEP B: weekly cells for the WHOLE record (single scale/unit), the
    # headline finding from the same series, and NRT provisional marking.
    nrt = _nrt_dates(db)
    weeks_all = _weekly(rows, nrt)
    finding = _finding(rows, weeks_all)
    # Phase 3A (defect 3) compatibility view: the transferred-era subset.
    weeks_transferred = [
        TransferredWeek(
            year=w.year, iso_week=w.iso_week, start_date=w.start_date,
            end_date=w.end_date, n_harmonized=w.n_harmonized,
            n_harmonized_lo=w.n_harmonized_lo,
            n_harmonized_hi=w.n_harmonized_hi, pct_n=w.pct_n,
            flag_high=w.flag_high, flag_low=w.flag_low,
            n_days_with_data=w.n_days_with_data,
        )
        for w in weeks_all if w.provenance in TRANSFERRED_PROVENANCE
    ]
    viirs_start = min(
        (c.date for c in cells if c.provenance in NATIVE_PROVENANCE),
        default=None,
    )
    native_cells = [c for c in cells if c.provenance in NATIVE_PROVENANCE]
    return CalendarResponse(
        aoi_id=aoi,
        baseline=BaselineInfo(
            start_year=cfg.baseline_start, end_year=cfg.baseline_end,
            kernel_days=15, z_threshold=cfg.anomaly_z_threshold,
            definition=(
                "VIIRS-native era: fixed "
                f"{cfg.baseline_start}-{cfg.baseline_end} DOY climatology, +/-15-day kernel, "
                "leave-one-year-out empirical percentiles (min 5 baseline years). "
                "Transferred era (2000-2011): anomalies are computed on WEEKLY "
                "aggregates against other transferred-era years only, never at "
                "daily level."
            ),
        ),
        cells=cells,
        seasons=[
            SeasonOut(
                year=s.year, onset_date=s.onset_date, end_date=s.end_date,
                season_length_days=s.season_length_days, peak_date=s.peak_date,
                peak_value=round(s.peak_value, 2), active_days=s.active_days,
                max_streak_days=s.max_streak_days, is_partial=s.is_partial,
            )
            for s in seasons
        ],
        viirs_start_date=viirs_start,
        transferred_weeks=[] if compact else weeks_transferred,
        weekly_cells=[] if compact else weeks_all,
        finding=finding,
        summary=CalendarSummary(
            max_n=max((c.n_harmonized for c in cells), default=0.0),
            max_frp=max((c.frp_harmonized_mw for c in cells), default=0.0),
            n_anomaly_high=sum(1 for c in cells if c.flag_high),
            n_anomaly_low=sum(1 for c in cells if c.flag_low),
            years=years,
            n_anomaly_high_native=sum(1 for c in native_cells if c.flag_high),
            n_anomaly_low_native=sum(1 for c in native_cells if c.flag_low),
            n_anomaly_high_weekly=sum(1 for w in weeks_all if w.flag_high),
            n_anomaly_low_weekly=sum(1 for w in weeks_all if w.flag_low),
        ),
    )
