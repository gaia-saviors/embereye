"""Month-of-year transfer factors on the COMPARABLE SUBSET (Phase 2).

Replaces the monthly power-law as the primary MODIS -> VIIRS-equivalent
transfer (audit verdict A7: LOYO bias of the power law -15% to -23%).

Design (Phase 2, approved 2026-11-02):
  * Comparable subset: DAYTIME, NON-STATIC detections only. The all-
    detection ratio is unstable (12-40) because S-NPP night rows are ~71%
    static sources MODIS barely sees; restricted to day + non-static the
    annual ratio is stable (7.4 / 7.2 / 7.4 over 2012-17 / 2012-21 /
    2022-25, range 5.8-9.5 on the Nile Delta record).
  * Factors are per month-of-year (raw range 3.2 Dec - 11.3 Sep), so one
    annual scalar would distort the seasonal shape.
  * Ratio-of-totals per month-of-year over the calibration window, then
    circular [1,2,1]/4 smoothing; months whose pooled MODIS total is
    below `min_month_total` fall back to the annual ratio (min-count
    guard - a month with a handful of detections has no stable ratio).
  * Validation: leave-one-year-out over the calibration window at THREE
    aggregation levels (annual total, calendar-month, ISO-week), each
    reporting MAPE, bias and an empirical 95% prediction interval. The
    interval that is carried into the series/UI matches the aggregation
    level being displayed: day view uses the annual-LOYO daily interval,
    week view the weekly-LOYO interval, month view the monthly-LOYO
    interval (v1.0.6 - previously the annual PI was applied per day and
    summed, understating month-level uncertainty).
  * Pooled (Terra+Aqua) vs per-platform: both fitted + validated; the
    per-platform variant wins only if its LOYO MAPE is lower by more
    than `per_platform_margin` (1 percentage point); otherwise pooled is
    adopted (simpler, more robust). The Terra-only era 2000-11..2002-06
    always uses the Terra-specific factor because the pooled factor
    calibrates the two-satellite mix.
  * Calibration window 2012-2022: MODIS overpass times are stable until
    2022 (drift starts 2023: Terra 8.8->7.1 UTC, Aqua 11.2->13.1 UTC) and
    the platform comparison is same-era.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np

MOYS = tuple(range(1, 13))
DEFAULT_MIN_MONTH_TOTAL = 100
DEFAULT_PER_PLATFORM_MARGIN = 0.01


def _smooth_circular(raw: dict[int, float]) -> dict[int, float]:
    """[1,2,1]/4 weighted circular moving average over the month ring."""
    out: dict[int, float] = {}
    for i, m in enumerate(MOYS):
        prev, nxt = MOYS[(i - 1) % 12], MOYS[(i + 1) % 12]
        out[m] = 0.25 * raw[prev] + 0.5 * raw[m] + 0.25 * raw[nxt]
    return out


@dataclass(slots=True)
class MoyFit:
    """Fitted month-of-year transfer for one platform grouping."""
    factors: dict[int, float]              # month-of-year -> factor
    annual_ratio: float
    min_month_total: int
    smoothed: bool
    fallback_months: list[int] = field(default_factory=list)

    def predict(self, modis_counts: dict[int, float]) -> float:
        """VIIRS-equivalent total for a period whose counts are given by
        month-of-year (any aggregation >= daily: the factor is constant
        within a month, so daily/weekly/monthly sums are consistent)."""
        return float(sum(self.factors.get(m, self.annual_ratio) * c
                         for m, c in modis_counts.items()))


@dataclass(slots=True)
class MoyTransfer:
    """The primary Phase 2 transfer: chosen variant + both LOYO reports."""
    variant: str                       # "pooled" | "per_platform"
    pooled: MoyFit
    terra: MoyFit
    aqua: MoyFit
    loyo_pooled: dict
    loyo_per_platform: dict
    loyo_monthly: dict
    loyo_weekly: dict
    calibration_years: list[int]
    min_month_total: int
    per_platform_margin: float

    @property
    def mape(self) -> float:
        chosen = self.loyo_pooled if self.variant == "pooled" else self.loyo_per_platform
        return float(chosen["mape"])

    @property
    def bias(self) -> float:
        chosen = self.loyo_pooled if self.variant == "pooled" else self.loyo_per_platform
        return float(chosen["bias"])

    @property
    def pi(self) -> tuple[float, float]:
        chosen = self.loyo_pooled if self.variant == "pooled" else self.loyo_per_platform
        return float(chosen["pi_low"]), float(chosen["pi_high"])

    @property
    def pi_monthly(self) -> tuple[float, float]:
        return float(self.loyo_monthly["pi_low"]), float(self.loyo_monthly["pi_high"])

    @property
    def pi_weekly(self) -> tuple[float, float]:
        return float(self.loyo_weekly["pi_low"]), float(self.loyo_weekly["pi_high"])

    def as_params(self) -> dict:
        lo, hi = self.pi
        mlo, mhi = self.pi_monthly
        wlo, whi = self.pi_weekly
        return {
            "design": "moy_factor_v1",
            "subset": "daytime_non_static",
            "variant": self.variant,
            "calibration_years": self.calibration_years,
            "min_month_total": self.min_month_total,
            "per_platform_margin": self.per_platform_margin,
            "factors_pooled": {str(k): round(v, 4) for k, v in self.pooled.factors.items()},
            "factors_terra": {str(k): round(v, 4) for k, v in self.terra.factors.items()},
            "factors_aqua": {str(k): round(v, 4) for k, v in self.aqua.factors.items()},
            "fallback_months": self.pooled.fallback_months,
            "loyo_pooled": {k: v for k, v in self.loyo_pooled.items()},
            "loyo_per_platform": {k: v for k, v in self.loyo_per_platform.items()},
            "mape": round(self.mape, 4),
            "bias": round(self.bias, 4),
            "pi_low": round(lo, 4),
            "pi_high": round(hi, 4),
            # Aggregation-level LOYO intervals (v1.0.6): the interval applied
            # at each UI aggregation equals the LOYO error distribution AT
            # that aggregation, never the annual PI rescaled.
            "mape_monthly": round(float(self.loyo_monthly["mape"]), 4),
            "bias_monthly": round(float(self.loyo_monthly["bias"]), 4),
            "pi_monthly_low": round(mlo, 4),
            "pi_monthly_high": round(mhi, 4),
            "n_monthly_errors": int(self.loyo_monthly["n_errors"]),
            "mape_weekly": round(float(self.loyo_weekly["mape"]), 4),
            "bias_weekly": round(float(self.loyo_weekly["bias"]), 4),
            "pi_weekly_low": round(wlo, 4),
            "pi_weekly_high": round(whi, 4),
            "n_weekly_errors": int(self.loyo_weekly["n_errors"]),
            "interval_policy": "aggregation-matched LOYO PI (day=annual, week=weekly, month=monthly)",
            "power_law_retired_to": "methods_comparison",
        }


def _fit_one(pairs_by_moy: dict[int, list[tuple[float, float]]],
             min_month_total: int, smoothed: bool) -> MoyFit:
    tot_m = sum(m for ps in pairs_by_moy.values() for m, _ in ps)
    tot_v = sum(v for ps in pairs_by_moy.values() for _, v in ps)
    annual = (tot_v / tot_m) if tot_m > 0 else 1.0
    raw: dict[int, float] = {}
    fallback: list[int] = []
    for m in MOYS:
        mm = sum(p[0] for p in pairs_by_moy.get(m, []))
        vv = sum(p[1] for p in pairs_by_moy.get(m, []))
        if mm >= min_month_total and mm > 0:
            raw[m] = vv / mm
        else:
            raw[m] = annual
            fallback.append(m)
    factors = _smooth_circular(raw) if smoothed else dict(raw)
    return MoyFit(factors, annual, min_month_total, smoothed, fallback)


def _loyo(pairs_by_year: dict[int, dict[int, tuple[float, float]]],
          min_month_total: int, smoothed: bool) -> dict:
    """Leave-one-year-out annual-total validation; empirical 95% PI."""
    errs: list[float] = []
    detail = []
    for held in sorted(pairs_by_year):
        by_moy: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for y, months in pairs_by_year.items():
            if y == held:
                continue
            for m, pair in months.items():
                by_moy[m].append(pair)
        f = _fit_one(by_moy, min_month_total, smoothed)
        modis_counts = {m: mm for m, (mm, _) in pairs_by_year[held].items()}
        pred = f.predict(modis_counts)
        actual = float(sum(vv for _, vv in pairs_by_year[held].values()))
        rel = (pred / actual - 1.0) if actual > 0 else 0.0
        errs.append(rel)
        detail.append({"year": held, "predicted": round(pred, 1),
                       "actual": round(actual, 1), "rel_error": round(rel, 4)})
    errs_arr = np.asarray(errs, dtype=np.float64)
    pi_low, pi_high = (float(np.percentile(errs_arr, 2.5)),
                       float(np.percentile(errs_arr, 97.5)))
    return {
        "mape": round(float(np.mean(np.abs(errs_arr))), 4),
        "bias": round(float(np.mean(errs_arr)), 4),
        "pi_low": round(pi_low, 4),
        "pi_high": round(pi_high, 4),
        "n_folds": len(errs),
        "detail": detail,
    }


def _loyo_per_platform(terra_by_year: dict, aqua_by_year: dict,
                       min_month_total: int, smoothed: bool) -> dict:
    errs: list[float] = []
    detail = []
    for held in sorted(terra_by_year):
        t_by: dict[int, list[tuple[float, float]]] = defaultdict(list)
        a_by: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for y in terra_by_year:
            if y == held:
                continue
            for m, pair in terra_by_year[y].items():
                t_by[m].append(pair)
            for m, pair in aqua_by_year[y].items():
                a_by[m].append(pair)
        ft, fa = _fit_one(t_by, min_month_total, smoothed), _fit_one(a_by, min_month_total, smoothed)
        pred = (ft.predict({m: mm for m, (mm, _) in terra_by_year[held].items()})
                + fa.predict({m: mm for m, (mm, _) in aqua_by_year[held].items()}))
        actual = float(sum(vv for _, vv in terra_by_year[held].values())
                       + sum(vv for _, vv in aqua_by_year[held].values()))
        rel = (pred / actual - 1.0) if actual > 0 else 0.0
        errs.append(rel)
        detail.append({"year": held, "predicted": round(pred, 1),
                       "actual": round(actual, 1), "rel_error": round(rel, 4)})
    errs_arr = np.asarray(errs, dtype=np.float64)
    pi_low, pi_high = (float(np.percentile(errs_arr, 2.5)),
                       float(np.percentile(errs_arr, 97.5)))
    return {
        "mape": round(float(np.mean(np.abs(errs_arr))), 4),
        "bias": round(float(np.mean(errs_arr)), 4),
        "pi_low": round(pi_low, 4),
        "pi_high": round(pi_high, 4),
        "n_folds": len(errs),
        "detail": detail,
    }


def _loyo_monthly(pairs_by_year: dict[int, dict[int, tuple[float, float]]],
                  min_month_total: int, smoothed: bool) -> dict:
    """LOYO validated at CALENDAR-MONTH aggregation.

    For each held-out year the factors are refitted on the remaining years,
    then EVERY month of the held year is predicted as
    `factor(month) x comparable MODIS total` and compared with the actual
    comparable VIIRS total of that month. Months with no VIIRS comparable
    observations contribute no relative error (division by zero) - they are
    counted, never silently dropped.
    """
    errs: list[float] = []
    skipped = 0
    detail = []
    for held in sorted(pairs_by_year):
        by_moy: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for y, months in pairs_by_year.items():
            if y == held:
                continue
            for m, pair in months.items():
                by_moy[m].append(pair)
        f = _fit_one(by_moy, min_month_total, smoothed)
        year_errs: list[float] = []
        for m in range(1, 13):
            mm, vv = pairs_by_year[held][m]
            if vv <= 0:
                skipped += 1
                continue
            pred = f.factors.get(m, f.annual_ratio) * mm
            year_errs.append(pred / vv - 1.0)
        errs.extend(year_errs)
        detail.append({"year": held, "n_months_scored": len(year_errs),
                       "mape_year": round(float(np.mean(np.abs(year_errs))), 4)
                       if year_errs else None})
    errs_arr = np.asarray(errs, dtype=np.float64)
    return {
        "mape": round(float(np.mean(np.abs(errs_arr))), 4) if errs_arr.size else 0.0,
        "bias": round(float(np.mean(errs_arr)), 4) if errs_arr.size else 0.0,
        "pi_low": round(float(np.percentile(errs_arr, 2.5)), 4) if errs_arr.size else 0.0,
        "pi_high": round(float(np.percentile(errs_arr, 97.5)), 4) if errs_arr.size else 0.0,
        "n_errors": int(errs_arr.size),
        "n_months_skipped_no_viirs": skipped,
        "detail": detail,
    }


def _loyo_weekly(pairs_by_year: dict[int, dict[int, tuple[float, float]]],
                 daily_by_year: dict[int, tuple],
                 min_month_total: int, smoothed: bool) -> dict:
    """LOYO validated at ISO-WEEK aggregation.

    `daily_by_year[year] = (date_ords, modis_counts, viirs_counts)` holds the
    comparable-subset DAILY counts of the calibration window. For each
    held-out year the factors are refitted on the other years' monthly pairs,
    then every ISO week whose Monday falls in the held year is predicted as
    the per-day sum `factor(month(d)) x modis(d)` and compared with the
    actual VIIRS total of the SAME day set (boundary weeks are scored on
    their within-year days only, so prediction and truth always span the
    identical days). Weeks with no VIIRS comparable observations are
    skipped and counted.
    """
    errs: list[float] = []
    skipped = 0
    detail = []
    for held in sorted(pairs_by_year):
        by_moy: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for y, months in pairs_by_year.items():
            if y == held:
                continue
            for m, pair in months.items():
                by_moy[m].append(pair)
        f = _fit_one(by_moy, min_month_total, smoothed)
        ords, m_arr, v_arr = daily_by_year[held]
        ords = np.asarray(ords, dtype=np.int64)
        m_arr = np.asarray(m_arr, dtype=np.float64)
        v_arr = np.asarray(v_arr, dtype=np.float64)
        weeks: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, o in enumerate(ords):
            iso = date.fromordinal(int(o)).isocalendar()
            weeks[(iso[0], iso[1])].append(i)
        year_errs: list[float] = []
        for _wk, idxs in sorted(weeks.items()):
            vv = float(np.sum(v_arr[idxs]))
            if vv <= 0:
                skipped += 1
                continue
            pred = 0.0
            for i in idxs:
                month = date.fromordinal(int(ords[i])).month
                pred += f.factors.get(month, f.annual_ratio) * float(m_arr[i])
            year_errs.append(pred / vv - 1.0)
        errs.extend(year_errs)
        detail.append({"year": held, "n_weeks_scored": len(year_errs),
                       "mape_year": round(float(np.mean(np.abs(year_errs))), 4)
                       if year_errs else None})
    errs_arr = np.asarray(errs, dtype=np.float64)
    return {
        "mape": round(float(np.mean(np.abs(errs_arr))), 4) if errs_arr.size else 0.0,
        "bias": round(float(np.mean(errs_arr)), 4) if errs_arr.size else 0.0,
        "pi_low": round(float(np.percentile(errs_arr, 2.5)), 4) if errs_arr.size else 0.0,
        "pi_high": round(float(np.percentile(errs_arr, 97.5)), 4) if errs_arr.size else 0.0,
        "n_errors": int(errs_arr.size),
        "n_weeks_skipped_no_viirs": skipped,
        "detail": detail,
    }


def fit_moy_transfer(
    pooled_by_year: dict[int, dict[int, tuple[float, float]]],
    terra_by_year: dict[int, dict[int, tuple[float, float]]],
    aqua_by_year: dict[int, dict[int, tuple[float, float]]],
    min_month_total: int = DEFAULT_MIN_MONTH_TOTAL,
    smoothed: bool = True,
    per_platform_margin: float = DEFAULT_PER_PLATFORM_MARGIN,
    daily_by_year: dict[int, tuple] | None = None,
) -> MoyTransfer | None:
    """Fit the Phase 2 month-of-year transfer on the comparable subset.

    Each `*_by_year[year][month_of_year] = (modis_count, viirs_count)`.
    `daily_by_year` (optional) enables the ISO-week LOYO validation:
    `{year: (date_ords, modis_counts, viirs_counts)}`.
    """
    years = sorted(pooled_by_year)
    if len(years) < 3:
        return None
    def _to_moy(pairs_by_year):
        by_moy: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for months in pairs_by_year.values():
            for m, pair in months.items():
                by_moy[m].append(pair)
        return by_moy
    pooled = _fit_one(_to_moy(pooled_by_year), min_month_total, smoothed)
    terra = _fit_one(_to_moy(terra_by_year), min_month_total, smoothed)
    aqua = _fit_one(_to_moy(aqua_by_year), min_month_total, smoothed)
    loyo_p = _loyo(pooled_by_year, min_month_total, smoothed)
    loyo_s = _loyo_per_platform(terra_by_year, aqua_by_year, min_month_total, smoothed)
    variant = ("per_platform"
               if loyo_s["mape"] < loyo_p["mape"] - per_platform_margin
               else "pooled")
    # Aggregation-level validation (v1.0.6): monthly always; weekly when the
    # caller supplied daily counts. Both run on the CHOSEN variant's layout
    # (pooled pairs - the per-platform weekly variant would need daily
    # counts split by platform and is only marginally different).
    loyo_monthly = _loyo_monthly(pooled_by_year, min_month_total, smoothed)
    if daily_by_year:
        loyo_weekly = _loyo_weekly(pooled_by_year, daily_by_year,
                                   min_month_total, smoothed)
    else:
        loyo_weekly = {"mape": 0.0, "bias": 0.0, "pi_low": 0.0, "pi_high": 0.0,
                       "n_errors": 0, "n_weeks_skipped_no_viirs": 0,
                       "detail": [], "note": "daily counts not supplied"}
    return MoyTransfer(
        variant=variant, pooled=pooled, terra=terra, aqua=aqua,
        loyo_pooled=loyo_p, loyo_per_platform=loyo_s,
        loyo_monthly=loyo_monthly, loyo_weekly=loyo_weekly,
        calibration_years=years, min_month_total=min_month_total,
        per_platform_margin=per_platform_margin,
    )


def transfer_interval_factor(fit: MoyTransfer) -> tuple[float, float]:
    """Multiplicative interval bounds (1 + q2.5, 1 + q97.5) applied to any
    transferred aggregate; constant within a month so daily/weekly/monthly
    sums of lo/hi equal the aggregated lo/hi."""
    lo, hi = fit.pi
    return 1.0 + lo, 1.0 + hi
