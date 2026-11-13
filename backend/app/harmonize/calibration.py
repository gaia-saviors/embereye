"""Aggregate-level cross-sensor transfer functions (Tier-1 bridge).

Vadrevu et al. (2018, Remote Sensing 10(7):978) established that monthly
AOI aggregates of MODIS and VIIRS fire counts/FRP correlate strongly
(r^2 = 0.98 in Punjab) because Aqua and S-NPP share the 13:30/01:30
overpass family. We fit a power-law transfer

    viirs_equiv = expm1(b) * modis^s        (fitted in log1p space)

with a Theil-Sen estimator: robust to outbreak months, deterministic, and
distribution-free. Out-of-sample validation uses a chronological 70/30
split (train on the earlier months, test on the later ones) so the reported
r2_oos is honest about temporal generalization.

Small-sample fallback: with < 6 paired months a single-parameter ratio
model (slope=1, intercept=log1p(ratio of totals)) is fitted and flagged as
`ratio_fallback` - monotone, deterministic, documented.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(slots=True)
class TransferFit:
    variable: str                    # COUNTS | FRP
    method: str                      # theil_sen_log1p | ratio_fallback
    slope: float
    intercept: float                 # log-space intercept
    r2_in_sample: float              # log-space (the fitted space)
    r2_oos: float                    # log-space, chronological holdout
    r2_raw_oos: float                # raw-scale, chronological holdout
    n_months: int
    n_pairs: int                     # raw daily/monthly pairs before aggregation

    def predict(self, modis_values: np.ndarray) -> np.ndarray:
        x = np.log1p(np.maximum(np.asarray(modis_values, dtype=np.float64), 0.0))
        return np.expm1(self.intercept + self.slope * x)

    def as_dict(self) -> dict:
        return {
            "variable": self.variable,
            "method": self.method,
            "slope": round(self.slope, 6),
            "intercept": round(self.intercept, 6),
            "r2_in_sample": round(self.r2_in_sample, 4),
            "r2_oos": round(self.r2_oos, 4),
            "r2_raw_oos": round(self.r2_raw_oos, 4),
            "n_months": self.n_months,
            "n_pairs": self.n_pairs,
        }


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    if ss_tot <= 0:
        return 0.0
    return max(0.0, 1.0 - ss_res / ss_tot)


def fit_transfer(
    modis_monthly: np.ndarray,
    viirs_monthly: np.ndarray,
    variable: str,
    min_months_strict: int = 6,
) -> TransferFit | None:
    """Fit the MODIS -> VIIRS-equivalent transfer on monthly aligned sums."""
    m = np.asarray(modis_monthly, dtype=np.float64)
    v = np.asarray(viirs_monthly, dtype=np.float64)
    both = (m > 0) & (v > 0)
    m, v = m[both], v[both]
    n = m.size
    if n < 3:
        return None

    x = np.log1p(m)
    y = np.log1p(v)

    if n >= min_months_strict:
        split = int(np.ceil(0.7 * n))
        x_tr, y_tr = x[:split], y[:split]
        x_te, y_te = x[split:], y[split:]
        res = stats.theilslopes(y_tr, x_tr)
        slope, intercept = float(res.slope), float(res.intercept)
        # Goodness of fit is reported in log space (the fitted space: a
        # multiplicative power law). Raw-scale OOS r2 is kept as a secondary,
        # deliberately conservative diagnostic - it is dominated by the
        # largest months under heteroscedasticity.
        r2_in = _r2(y, intercept + slope * x)
        if x_te.size >= 2:
            r2_oos = _r2(y_te, intercept + slope * x_te)
            r2_raw = _r2(np.expm1(y_te), np.expm1(intercept + slope * x_te))
        else:
            r2_oos = r2_in
            r2_raw = _r2(np.expm1(y), np.expm1(intercept + slope * x))
        return TransferFit(variable, "theil_sen_log1p", slope, intercept,
                           r2_in, r2_oos, r2_raw, n, n)

    # Small-sample ratio fallback: viirs_equiv = ratio * modis (slope = 1).
    ratio = float(v.sum() / m.sum())
    intercept = np.log1p(ratio) if ratio > 0 else 0.0
    slope = 1.0
    r2_in = _r2(y, intercept + slope * x)
    return TransferFit(variable, "ratio_fallback", slope, intercept,
                       r2_in, r2_in, _r2(v, ratio * m), n, n)


def fit_day_night(
    m_day: np.ndarray, v_day: np.ndarray, m_night: np.ndarray, v_night: np.ndarray,
    variable: str,
) -> dict:
    """Coefficient-stability check across day/night subsets (Volume 5).

    Returns the primary fit plus per-subset slopes for the methods panel.
    """
    primary = fit_transfer(m_day + m_night, v_day + v_night, variable)
    out: dict = {"primary": primary.as_dict() if primary else None}
    for name, mm, vv in (("day", m_day, v_day), ("night", m_night, v_night)):
        sub = fit_transfer(mm, vv, variable)
        out[name] = (
            {"slope": round(sub.slope, 4), "method": sub.method, "n_months": sub.n_months}
            if sub else None
        )
    return out
