"""Day-of-year climatology, anomaly flags and fire-season metrics.

Recipe (research base R3 §8.2, standard practice in fire-climatology
reports): per AOI, build a DOY climatology of the harmonized daily series
over a FIXED baseline window with a +/-15-day kernel; flag z-score
exceedances; report anomalies in counts and FRP. Feb-29 handling: DOY keys
are calendar (month, day) slots, so leap days borrow their neighbours
instead of shifting all later DOYs.

Fire season definition (R3 §8.1): onset/end = first/last day the 7-day
rolling sum of harmonized detections exceeds 10% of that year's peak
7-day sum; peak day, active days and longest consecutive active streak
complete the annual card.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

LEAP_CAL = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
NONLEAP_CAL = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _doy_slot(d: date) -> int:
    """Calendar-aligned 1..366 slot.

    Slot = day-of-year, with a phantom Feb 29 inserted in NON-leap years
    (dates from Mar 1 shift +1). Result: Feb 29 -> slot 60 and Mar 1 ->
    slot 61 in every year, so +/-15-day windows align across leap and
    non-leap years instead of drifting by one day.
    """
    cal = LEAP_CAL if _is_leap(d.year) else NONLEAP_CAL
    doy = sum(cal[: d.month - 1]) + d.day
    if not _is_leap(d.year) and doy >= 60:
        return doy + 1
    return doy


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


@dataclass(slots=True)
class Climatology:
    slot_mean_n: np.ndarray        # (366,)
    slot_std_n: np.ndarray
    slot_p10_n: np.ndarray
    slot_p90_n: np.ndarray
    slot_mean_frp: np.ndarray
    z_floor: float
    baseline_years: list[int]

    def __post_init__(self) -> None:
        # Variance floor: quiet DOYs must not produce explosive z-scores.
        self.slot_std_n = np.maximum(self.slot_std_n, np.maximum(0.5, 0.15 * self.slot_mean_n))


def build_climatology(
    date_ords: np.ndarray,
    n_h: np.ndarray,
    frp_h: np.ndarray,
    baseline_years: tuple[int, int],
    kernel_days: int = 15,
) -> Climatology:
    y0, y1 = baseline_years
    year_arr = np.array([date.fromordinal(int(o)).year for o in date_ords])
    slots = np.array([_doy_slot(date.fromordinal(int(o))) for o in date_ords])
    in_base = (year_arr >= y0) & (year_arr <= y1)

    mean_n = np.zeros(366)
    std_n = np.zeros(366)
    p10_n = np.zeros(366)
    p90_n = np.zeros(366)
    mean_frp = np.zeros(366)
    offsets = np.arange(-kernel_days, kernel_days + 1)
    _ = offsets  # documented kernel width; window computed per slot below
    for slot in range(1, 367):
        # window membership with wraparound through the 366-slot ring
        diff = np.abs((slots - slot + 183) % 366 - 183)
        mask = in_base & (diff <= kernel_days)
        vals_n = n_h[mask]
        vals_f = frp_h[mask]
        if vals_n.size:
            mean_n[slot - 1] = float(vals_n.mean())
            std_n[slot - 1] = float(vals_n.std(ddof=0))
            p10_n[slot - 1] = float(np.percentile(vals_n, 10))
            p90_n[slot - 1] = float(np.percentile(vals_n, 90))
            mean_frp[slot - 1] = float(vals_f.mean())
    return Climatology(mean_n, std_n, p10_n, p90_n, mean_frp,
                       z_floor=0.5, baseline_years=[y0, y1])


def anomaly_flags(
    clim: Climatology,
    date_ords: np.ndarray,
    n_h: np.ndarray,
    frp_h: np.ndarray,
    z_threshold: float = 2.0,
    pct_high: float = 95.0,
) -> dict[str, np.ndarray]:
    """Phase 1 parametric flags - kept for FRP z only.

    The COUNTS flags are rebuilt in Phase 2 by `loyo_percentile_flags`
    (leave-one-year-out EMPIRICAL percentile + min-count guard); this
    function's parametric `norm.cdf` mapping biased quiet DOYs.
    """
    slots = np.array([_doy_slot(date.fromordinal(int(o))) for o in date_ords])
    mean_n = clim.slot_mean_n[slots - 1]
    std_n = clim.slot_std_n[slots - 1]
    z_n = (n_h - mean_n) / std_n
    # FRP z uses its own climatology mean/std analog.
    frp_mean = clim.slot_mean_frp[slots - 1]
    frp_std = np.maximum(0.15 * frp_mean, 0.5)
    z_frp = (frp_h - frp_mean) / frp_std
    flag_high = np.zeros(n_h.size, dtype=bool)
    flag_low = np.zeros(n_h.size, dtype=bool)
    return {"z_n": z_n, "z_frp": z_frp,
            "pct_n": np.full(n_h.size, np.nan),
            "flag_high": flag_high, "flag_low": flag_low}


def loyo_percentile_flags(
    date_ords: np.ndarray,
    n_h: np.ndarray,
    baseline_years: tuple[int, int],
    kernel_days: int = 15,
    min_years: int = 5,
    pct_high: float = 95.0,
    quiet_median_min: float = 1.0,
) -> dict[str, np.ndarray]:
    """Phase 2 anomaly rebuild: leave-one-year-out EMPIRICAL percentiles.

    For each day, the baseline sample is every baseline-window day inside
    the +/-kernel-day DOY ring EXCLUDING the day's own year (LOYO - the
    day never competes against a distribution that contains it). Flags:
      flag_high: empirical percentile >= pct_high (default 95) and n_h > 0
      flag_low:  "unusually quiet" - zero detections where the LOYO
                 baseline median expects activity (median > quiet_median_min)
    Min-count guard: a day is only flaggable when at least `min_years`
    DISTINCT baseline years contribute to its sample - otherwise pct is
    NaN and no flag is emitted (never flag on thin evidence).
    """
    years = np.array([date.fromordinal(int(o)).year for o in date_ords])
    slots = np.array([_doy_slot(date.fromordinal(int(o))) for o in date_ords])
    in_base = (years >= baseline_years[0]) & (years <= baseline_years[1])

    # Per-slot index of baseline samples (with wraparound kernel window).
    slot_members: dict[int, list[int]] = {}
    for s in range(1, 367):
        diff = np.abs((slots - s + 183) % 366 - 183)
        slot_members[s] = np.flatnonzero(in_base & (diff <= kernel_days))

    n = date_ords.size
    pct = np.full(n, np.nan)
    z_n = np.full(n, np.nan)
    flag_high = np.zeros(n, dtype=bool)
    flag_low = np.zeros(n, dtype=bool)

    for i in range(n):
        sample_idx = slot_members.get(int(slots[i]))
        if sample_idx is None or sample_idx.size == 0:
            continue
        own_year = years[i]
        keep = years[sample_idx] != own_year
        sample = n_h[sample_idx[keep]]
        if sample.size == 0:
            continue
        distinct_years = np.unique(years[sample_idx][keep]).size
        if distinct_years < min_years:
            continue  # min-count guard: never flag on thin evidence
        p = 100.0 * float(np.count_nonzero(sample <= n_h[i])) / sample.size
        pct[i] = p
        mu = float(sample.mean())
        floor = max(0.5, 0.15 * mu)
        z_n[i] = (n_h[i] - mu) / floor if floor > 0 else 0.0
        if n_h[i] > 0 and p >= pct_high:
            flag_high[i] = True
        if n_h[i] == 0 and float(np.median(sample)) > quiet_median_min:
            flag_low[i] = True

    return {"z_n": z_n, "z_frp": np.full(n, np.nan), "pct_n": pct,
            "flag_high": flag_high, "flag_low": flag_low}


@dataclass(slots=True)
class SeasonCard:
    year: int
    onset_ord: int | None
    end_ord: int | None
    season_length_days: int | None
    peak_ord: int | None
    peak_value: float
    active_days: int
    max_streak_days: int


def season_metrics(
    date_ords: np.ndarray, n_h: np.ndarray, years: list[int]
) -> list[SeasonCard]:
    """Season = the shortest contiguous window around the 7-day-smoothed
    peak that contains >= 80% of that year's harmonized detections.

    (Pending fix, STEP B scope: replaces the old "10% of the annual peak"
    rule, which made season length hypersensitive to isolated single-day
    spikes.) Algorithm per year: r = trailing 7-day rolling sum; total =
    sum of the year's daily detections; start from the peak day and grow
    the window one day at a time, always absorbing the side (left/right)
    with the larger rolling value, until the window holds >= 80% of the
    annual total. Deterministic and monotone; ties break to the left."""
    if date_ords.size == 0:
        return []
    cards: list[SeasonCard] = []
    lo_ord = int(date_ords.min())
    hi_ord = int(date_ords.max())
    full = np.arange(lo_ord, hi_ord + 1, dtype=np.int64)
    series = np.zeros(full.size, dtype=np.float64)
    idx = np.searchsorted(full, date_ords)
    series[idx] = n_h
    # trailing 7-day sum (inclusive) - smoothing only, used to pick the
    # growth order; the 80% accounting runs on the RAW daily series.
    kernel = np.ones(7, dtype=np.float64)
    rolling = np.convolve(series, kernel, mode="full")[: series.size]

    for year in years:
        y_start = date(year, 1, 1).toordinal()
        y_end = date(year, 12, 31).toordinal()
        sel = (full >= y_start) & (full <= y_end)
        if not sel.any():
            continue
        r = rolling[sel]
        days = full[sel]
        raw_total = float(series[sel].sum())
        peak = float(r.max()) if r.size else 0.0
        if raw_total <= 0 or peak <= 0:
            cards.append(SeasonCard(
                year=year, onset_ord=None, end_ord=None,
                season_length_days=None,
                peak_ord=None, peak_value=peak,
                active_days=int((series[sel] > 0).sum()),
                max_streak_days=_max_streak(series[sel] > 0),
            ))
            continue
        peak_i = int(r.argmax())
        left, right = peak_i, peak_i  # inclusive indices into days
        covered = float(series[sel][peak_i])
        target = 0.80 * raw_total
        while covered < target and (left > 0 or right < r.size - 1):
            grow_left = left > 0
            grow_right = right < r.size - 1
            if grow_left and grow_right:
                if r[left - 1] >= r[right + 1]:
                    left -= 1
                    covered += float(series[sel][left])
                else:
                    right += 1
                    covered += float(series[sel][right])
            elif grow_left:
                left -= 1
                covered += float(series[sel][left])
            else:
                right += 1
                covered += float(series[sel][right])
        onset = int(days[left])
        end = int(days[right])
        peak_ord = int(days[peak_i])
        active = series[sel] > 0
        streak = _max_streak(active)
        cards.append(SeasonCard(
            year=year,
            onset_ord=onset,
            end_ord=end,
            season_length_days=end - onset + 1,
            peak_ord=peak_ord,
            peak_value=peak,
            active_days=int(active.sum()),
            max_streak_days=streak,
        ))
    return cards


def _max_streak(active: np.ndarray) -> int:
    best = 0
    cur = 0
    for flag in active:
        if flag:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
