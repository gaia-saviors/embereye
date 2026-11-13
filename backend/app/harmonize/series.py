"""Daily aggregates and the harmonized fused series.

Hotspot rows are loaded once per AOI into numpy arrays (the HotspotArrays
record) and every downstream stage is a pure array function - deterministic,
allocation-cheap, and independently testable.

Phase 2 harmonized target: the COMPARABLE SUBSET (daytime, non-static) in
S-NPP-equivalent units. Era logic per calendar day:
  MODIS_TRANSFERRED (day < VIIRS start)  n_h = moy_factor x comparable MODIS
  VIIRS_NATIVE      (day >= VIIRS start) n_h = comparable reference VIIRS
                                         (NOAA-20 / factor after S-NPP end)
  NO_DATA                                outside the record span
Prediction intervals (LOYO) ride along in n_harmonized_lo/hi. The old
seam-blended design (MODIS_ONLY / OVERLAP_FUSED / VIIRS_FORWARD) is retired;
`seam_weight` survives only as the 0/1 era indicator.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Hotspot
from .calibration import TransferFit

SAT_CODES = {"Terra": 0, "Aqua": 1, "SNPP": 2, "N20": 3, "N21": 4}
SAT_NAMES = {v: k for k, v in SAT_CODES.items()}


@dataclass(slots=True)
class HotspotArrays:
    aoi_id: str
    t_unix: np.ndarray            # float64 seconds
    date_ord: np.ndarray          # int32 local_date.toordinal()
    lat: np.ndarray
    lon: np.ndarray
    frp_mw: np.ndarray            # NaN when missing
    t4_k: np.ndarray              # NaN when missing
    t11_k: np.ndarray
    sensor: np.ndarray            # 0=MODIS 1=VIIRS
    satellite: np.ndarray         # SAT_CODES
    conf_class: np.ndarray        # 0=LOW 1=NOMINAL 2=HIGH
    daynight: np.ndarray          # 0=D 1=N
    area_km2: np.ndarray
    static_source: np.ndarray     # bool
    saturated: np.ndarray         # bool
    band21_fallback: np.ndarray   # bool

    def __len__(self) -> int:
        return int(self.t_unix.size)

    def subset(self, mask: np.ndarray) -> "HotspotArrays":
        sel = np.flatnonzero(mask)
        return HotspotArrays(
            aoi_id=self.aoi_id,
            t_unix=self.t_unix[sel], date_ord=self.date_ord[sel],
            lat=self.lat[sel], lon=self.lon[sel],
            frp_mw=self.frp_mw[sel], t4_k=self.t4_k[sel], t11_k=self.t11_k[sel],
            sensor=self.sensor[sel], satellite=self.satellite[sel],
            conf_class=self.conf_class[sel], daynight=self.daynight[sel],
            area_km2=self.area_km2[sel], static_source=self.static_source[sel],
            saturated=self.saturated[sel], band21_fallback=self.band21_fallback[sel],
        )


def load_aoi_arrays(db: Session, aoi_id: str) -> HotspotArrays:
    rows = db.execute(
        select(
            Hotspot.acq_dt_utc, Hotspot.local_date, Hotspot.lat, Hotspot.lon,
            Hotspot.frp_mw, Hotspot.brightness_t4_k, Hotspot.brightness_t11_k,
            Hotspot.sensor_family, Hotspot.satellite, Hotspot.confidence_class,
            Hotspot.daynight, Hotspot.pixel_area_km2, Hotspot.static_source,
            Hotspot.saturated, Hotspot.band21_fallback,
        ).where(Hotspot.aoi_id == aoi_id).order_by(Hotspot.acq_dt_utc, Hotspot.id)
    ).all()
    return _rows_to_arrays(aoi_id, rows)


def load_arrays_spatial(db: Session, aoi) -> HotspotArrays:
    """User-drawn AOIs: bbox prefilter (lat/lon index) + exact point-in-polygon.

    The AOI model exposes west/south/east/north and a GeoJSON polygon.
    """
    from ..geometry import points_in_polygon

    rows = db.execute(
        select(
            Hotspot.acq_dt_utc, Hotspot.local_date, Hotspot.lat, Hotspot.lon,
            Hotspot.frp_mw, Hotspot.brightness_t4_k, Hotspot.brightness_t11_k,
            Hotspot.sensor_family, Hotspot.satellite, Hotspot.confidence_class,
            Hotspot.daynight, Hotspot.pixel_area_km2, Hotspot.static_source,
            Hotspot.saturated, Hotspot.band21_fallback,
        ).where(
            (Hotspot.lat >= aoi.south) & (Hotspot.lat <= aoi.north)
            & (Hotspot.lon >= aoi.west) & (Hotspot.lon <= aoi.east)
        ).order_by(Hotspot.acq_dt_utc, Hotspot.id)
    ).all()
    if rows:
        ring = json.loads(aoi.geojson)["coordinates"][0] if aoi.geojson else None
        lats = np.array([r[2] for r in rows])
        lons = np.array([r[3] for r in rows])
        inside = points_in_polygon(lons, lats, ring)
        rows = [r for r, keep in zip(rows, inside) if keep]
    return _rows_to_arrays(aoi.id, rows)


def _rows_to_arrays(aoi_id: str, rows) -> HotspotArrays:
    n = len(rows)
    if n == 0:
        return HotspotArrays(
            aoi_id=aoi_id,
            t_unix=np.empty(0), date_ord=np.empty(0, dtype=np.int32),
            lat=np.empty(0), lon=np.empty(0), frp_mw=np.empty(0), t4_k=np.empty(0),
            t11_k=np.empty(0), sensor=np.empty(0, dtype=np.int8),
            satellite=np.empty(0, dtype=np.int8), conf_class=np.empty(0, dtype=np.int8),
            daynight=np.empty(0, dtype=np.int8), area_km2=np.empty(0),
            static_source=np.empty(0, dtype=bool), saturated=np.empty(0, dtype=bool),
            band21_fallback=np.empty(0, dtype=bool),
        )
    conf_rank = {"LOW": 0, "NOMINAL": 1, "HIGH": 2}
    sensor_rank = {"MODIS": 0, "VIIRS": 1}
    dn_rank = {"D": 0, "N": 1}
    t_unix = np.array([r[0].timestamp() for r in rows], dtype=np.float64)
    date_ord = np.array([r[1].toordinal() for r in rows], dtype=np.int32)
    lat = np.array([r[2] for r in rows], dtype=np.float64)
    lon = np.array([r[3] for r in rows], dtype=np.float64)
    frp = np.array([r[4] if r[4] is not None else np.nan for r in rows], dtype=np.float64)
    t4 = np.array([r[5] if r[5] is not None else np.nan for r in rows], dtype=np.float64)
    t11 = np.array([r[6] if r[6] is not None else np.nan for r in rows], dtype=np.float64)
    sensor = np.array([sensor_rank[r[7]] for r in rows], dtype=np.int8)
    satellite = np.array([SAT_CODES.get(r[8], -1) for r in rows], dtype=np.int8)
    conf = np.array([conf_rank[r[9]] for r in rows], dtype=np.int8)
    dn = np.array([dn_rank[r[10]] for r in rows], dtype=np.int8)
    area = np.array([r[11] for r in rows], dtype=np.float64)
    static = np.array([bool(r[12]) for r in rows], dtype=bool)
    satur = np.array([bool(r[13]) for r in rows], dtype=bool)
    b21 = np.array([bool(r[14]) for r in rows], dtype=bool)
    return HotspotArrays(
        aoi_id=aoi_id, t_unix=t_unix, date_ord=date_ord, lat=lat, lon=lon,
        frp_mw=frp, t4_k=t4, t11_k=t11, sensor=sensor, satellite=satellite,
        conf_class=conf, daynight=dn, area_km2=area,
        static_source=static, saturated=satur, band21_fallback=b21,
    )


# --------------------------------------------------------------------------
# Daily aggregation
# --------------------------------------------------------------------------
@dataclass(slots=True)
class DailyBySatellite:
    date_ord: np.ndarray
    satellite: np.ndarray
    sensor: np.ndarray
    n_total: np.ndarray
    n_strict: np.ndarray
    n_day: np.ndarray
    n_night: np.ndarray
    frp_sum: np.ndarray
    frp_mean: np.ndarray
    area_sum: np.ndarray
    t4_mean: np.ndarray


def daily_aggregates(arr: HotspotArrays) -> DailyBySatellite:
    """Per (local_date, satellite) aggregates. Standard counts include
    static-source detections; strict counts exclude them."""
    if len(arr) == 0:
        return DailyBySatellite(*[np.empty(0) for _ in range(11)])  # type: ignore[arg-type]
    dates = np.unique(arr.date_ord)
    sats = np.unique(arr.satellite)
    order = np.lexsort((arr.satellite, arr.date_ord))
    d_s, sat_s = arr.date_ord[order], arr.satellite[order]
    sensor_s = arr.sensor[order]

    keys = np.stack([d_s, sat_s], axis=1)
    change = np.ones(len(keys), dtype=bool)
    if len(keys) > 1:
        change[1:] = np.any(keys[1:] != keys[:-1], axis=1)
    starts = np.flatnonzero(change)
    ends = np.append(starts[1:], len(keys))

    n = starts.size
    n_total = np.empty(n, dtype=np.int64)
    n_strict = np.empty(n, dtype=np.int64)
    n_day = np.empty(n, dtype=np.int64)
    n_night = np.empty(n, dtype=np.int64)
    frp_sum = np.empty(n, dtype=np.float64)
    frp_mean = np.empty(n, dtype=np.float64)
    area_sum = np.empty(n, dtype=np.float64)
    t4_mean = np.empty(n, dtype=np.float64)
    frp_all = arr.frp_mw[order]
    t4_all = arr.t4_k[order]
    area_all = arr.area_km2[order]
    static_all = arr.static_source[order]
    dn_all = arr.daynight[order]

    for i, (s, e) in enumerate(zip(starts, ends)):
        sl = slice(s, e)
        n_total[i] = e - s
        n_strict[i] = int(np.count_nonzero(~static_all[sl]))
        n_day[i] = int(np.count_nonzero(dn_all[sl] == 0))
        n_night[i] = int(np.count_nonzero(dn_all[sl] == 1))
        frp_sl = frp_all[sl]
        valid = np.isfinite(frp_sl)
        frp_sum[i] = float(np.sum(frp_sl[valid])) if valid.any() else 0.0
        frp_mean[i] = float(np.mean(frp_sl[valid])) if valid.any() else 0.0
        area_sum[i] = float(np.sum(area_all[sl]))
        t4_sl = t4_all[sl]
        t4_valid = np.isfinite(t4_sl)
        t4_mean[i] = float(np.mean(t4_sl[t4_valid])) if t4_valid.any() else 0.0

    return DailyBySatellite(
        date_ord=d_s[starts], satellite=sat_s[starts], sensor=sensor_s[starts],
        n_total=n_total, n_strict=n_strict, n_day=n_day, n_night=n_night,
        frp_sum=frp_sum, frp_mean=frp_mean, area_sum=area_sum, t4_mean=t4_mean,
    )


# --------------------------------------------------------------------------
# Harmonized series (Phase 2: comparable subset, reference platform)
# --------------------------------------------------------------------------
def seam_weight(date_ords: np.ndarray, seam_start_ord: int, seam_end_ord: int) -> np.ndarray:
    """Cosine-smoothed ramp 0 -> 1 across the seam window; 0 before, 1 after.

    Retired as a blending weight in Phase 2 (the harmonized series is now
    era-clean: transferred MODIS before the VIIRS start, native reference
    VIIRS after); kept as the 0/1 era indicator for schema stability.
    """
    d = np.asarray(date_ords, dtype=np.float64)
    if seam_end_ord <= seam_start_ord:
        return np.where(d < seam_start_ord, 0.0, 1.0)
    t = (d - seam_start_ord) / float(seam_end_ord - seam_start_ord)
    t = np.clip(t, 0.0, 1.0)
    return 0.5 * (1.0 - np.cos(np.pi * t))


def per_day_totals(arr: HotspotArrays, days: np.ndarray, mask: np.ndarray,
                   values: np.ndarray | None = None) -> np.ndarray:
    """Sum of `values` (or row counts when None) per calendar day, for the
    rows selected by `mask`. Days without rows contribute 0."""
    out = np.zeros(days.size, dtype=np.float64)
    sel = np.flatnonzero(mask)
    if sel.size == 0:
        return out
    d = arr.date_ord[sel]
    idx = np.searchsorted(days, d)
    valid = (idx < days.size) & (days[np.clip(idx, 0, days.size - 1)] == d)
    idx = idx[valid]
    if values is None:
        np.add.at(out, idx, 1.0)
    else:
        np.add.at(out, idx, values[sel][valid])
    return out


def continuity_factors(
    arr: HotspotArrays,
    reference_code: int,
    successor_code: int,
) -> dict:
    """Overlap-era continuity factors, successor -> reference units.

    Direction (Phase 2, item 5): factor = successor_total / reference_total
    over calendar months observed by both platforms. Months with detections
    from only one platform are excluded rather than treated as overlap.
    factor > 1 means the successor counts HIGHER, so the reference-equivalent
    value is successor / factor (NOT x factor). Day and night are fitted
    separately; their uncertainty bounds are the empirical 2.5/97.5
    percentiles of yearly ratios computed from those same shared months.
    """
    if len(arr) == 0:
        return {
            "shared_months": 0, "shared_months_day": 0,
            "shared_months_night": 0, "factor_day": None,
            "factor_night": None, "factor_day_lo": None,
            "factor_day_hi": None, "factor_night_lo": None,
            "factor_night_hi": None, "factor_lo": None,
            "factor_hi": None, "per_year": {}, "per_month": {},
        }

    is_ref = arr.satellite == reference_code
    is_suc = arr.satellite == successor_code
    day = arr.daynight == 0
    static = arr.static_source
    month_key = np.array([
        date.fromordinal(int(o)).year * 12
        + date.fromordinal(int(o)).month - 1
        for o in arr.date_ord
    ], dtype=np.int32)

    out: dict = {
        "shared_months": 0, "shared_months_day": 0,
        "shared_months_night": 0, "factor_day": None,
        "factor_night": None, "factor_day_lo": None,
        "factor_day_hi": None, "factor_night_lo": None,
        "factor_night_hi": None, "factor_lo": None,
        "factor_hi": None, "per_year": {}, "per_month": {},
    }
    shared_any: set[int] = set()
    for name, dn_mask in (("day", day), ("night", ~day)):
        m_ref = is_ref & dn_mask & ~static
        m_suc = is_suc & dn_mask & ~static
        ref_months, ref_counts = np.unique(month_key[m_ref], return_counts=True)
        suc_months, suc_counts = np.unique(month_key[m_suc], return_counts=True)
        ref_by_month = {int(k): int(v) for k, v in zip(ref_months, ref_counts)}
        suc_by_month = {int(k): int(v) for k, v in zip(suc_months, suc_counts)}
        shared = sorted(set(ref_by_month) & set(suc_by_month))
        if not shared:
            continue

        tot_ref = sum(ref_by_month[k] for k in shared)
        tot_suc = sum(suc_by_month[k] for k in shared)
        factor = tot_suc / tot_ref
        ref_by_year: dict[int, int] = defaultdict(int)
        suc_by_year: dict[int, int] = defaultdict(int)
        for key in shared:
            year = key // 12
            ref_by_year[year] += ref_by_month[key]
            suc_by_year[year] += suc_by_month[key]
        per_year = {
            year: suc_by_year[year] / ref_by_year[year]
            for year in sorted(ref_by_year) if ref_by_year[year] > 0
        }
        vals = np.asarray(list(per_year.values()), dtype=np.float64)
        if vals.size >= 3:
            factor_lo, factor_hi = (float(np.percentile(vals, 2.5)),
                                    float(np.percentile(vals, 97.5)))
        else:
            factor_lo, factor_hi = float(vals.min()), float(vals.max())

        out[f"factor_{name}"] = float(factor)
        out[f"factor_{name}_lo"] = factor_lo
        out[f"factor_{name}_hi"] = factor_hi
        out[f"shared_months_{name}"] = len(shared)
        out["per_year"][name] = {
            str(y): round(v, 4) for y, v in per_year.items()
        }
        out["per_month"][name] = {
            f"{key // 12:04d}-{key % 12 + 1:02d}": round(
                suc_by_month[key] / ref_by_month[key], 4
            )
            for key in shared
        }
        shared_any.update(shared)

    out["shared_months"] = len(shared_any)
    lows = [out[f"factor_{name}_lo"] for name in ("day", "night")
            if out[f"factor_{name}_lo"] is not None]
    highs = [out[f"factor_{name}_hi"] for name in ("day", "night")
             if out[f"factor_{name}_hi"] is not None]
    if lows:
        # Legacy envelope retained for older stored records and callers.
        out["factor_lo"] = min(lows)
        out["factor_hi"] = max(highs)
    return out


def harmonize_comparable(
    arr: HotspotArrays,
    start_ord: int,
    end_ord: int,
    transfer,                                # MoyTransfer | None
    frp_transfer,                            # MoyTransfer | None (FRP factors)
    viirs_start_ord: int,
    reference_code: int = 2,                 # SNPP
    successor_code: int = 3,                 # NOAA-20
    snpp_end_ord: int | None = None,         # reference end of delivery
    cont: dict | None = None,                # continuity_factors() output
) -> dict[str, np.ndarray]:
    """Build the Phase 2 harmonized ledger for [start_ord, end_ord].

    Headline population: the COMPARABLE SUBSET (daytime, non-static).
      era MODIS_TRANSFERRED (day < viirs_start_ord):
          n_h = month-of-year factor x comparable MODIS
                (Terra-only days use the Terra factor, the pooled factor
                calibrates the two-satellite mix)
          lo/hi = n_h x (1 + PI_low, 1 + PI_high) from the LOYO validation
      era VIIRS_NATIVE (day >= viirs_start_ord):
          reference platform (SNPP to snpp_end_ord, then NOAA-20 divided by
          the day/night continuity factors - never summed with SNPP);
          lo=hi=n_h for SNPP days, continuity-spread interval after.
    Separate layers from viirs_start_ord (no MODIS equivalent exists before):
      n_static = reference-platform static detections (night factor after
                 the reference end: flares are predominantly night rows)
      n_night  = reference-platform non-static night detections
    """
    days = np.arange(start_ord, end_ord + 1, dtype=np.int64)
    n_days = days.size
    z = np.zeros(n_days, dtype=np.float64)

    sat = arr.satellite
    is_terra = sat == 0
    is_aqua = sat == 1
    is_modis = arr.sensor == 0
    is_ref = sat == reference_code
    is_suc = sat == successor_code
    is_day = arr.daynight == 0
    is_static = arr.static_source

    comp_modis = is_day & ~is_static & is_modis
    comp_terra = comp_modis & is_terra
    comp_aqua = comp_modis & is_aqua
    comp_ref = is_day & ~is_static & is_ref
    comp_suc = is_day & ~is_static & is_suc
    static_ref = is_static & is_ref
    static_suc = is_static & is_suc
    night_ref = ~is_day & ~is_static & is_ref
    night_suc = ~is_day & ~is_static & is_suc

    frp = np.where(np.isfinite(arr.frp_mw), arr.frp_mw, 0.0)

    n_terra = per_day_totals(arr, days, comp_terra)
    n_aqua = per_day_totals(arr, days, comp_aqua)
    n_comp_modis = n_terra + n_aqua
    n_comp_ref = per_day_totals(arr, days, comp_ref)
    n_comp_suc = per_day_totals(arr, days, comp_suc)
    n_static_r = per_day_totals(arr, days, static_ref)
    n_static_s = per_day_totals(arr, days, static_suc)
    n_night_r = per_day_totals(arr, days, night_ref)
    n_night_s = per_day_totals(arr, days, night_suc)

    frp_terra = per_day_totals(arr, days, comp_terra, frp)
    frp_aqua = per_day_totals(arr, days, comp_aqua, frp)
    frp_comp_modis = frp_terra + frp_aqua
    frp_comp_ref = per_day_totals(arr, days, comp_ref, frp)
    frp_comp_suc = per_day_totals(arr, days, comp_suc, frp)

    n_modis_all = per_day_totals(arr, days, is_modis)
    n_viirs_ref_all = per_day_totals(arr, days, is_ref)

    first_obs = int(arr.date_ord.min()) if len(arr) else start_ord
    last_obs = int(arr.date_ord.max()) if len(arr) else end_ord
    no_data = (days < first_obs) | (days > last_obs)

    era_modis = (days < viirs_start_ord) & ~no_data
    era_viirs = (days >= viirs_start_ord) & ~no_data

    # ---- transfer factors ---------------------------------------------------
    month_of = np.array([date.fromordinal(int(o)).month for o in days], dtype=np.int64)
    aqua_start = int(arr.date_ord[comp_aqua].min()) if comp_aqua.any() else None

    def moy_factor(fit, month: int) -> float:
        if fit is None:
            return 1.0
        return float(fit.factors.get(int(month), fit.annual_ratio))

    pi_lo = pi_hi = 0.0
    frp_pi_lo = frp_pi_hi = 0.0
    if transfer is not None:
        pi_lo, pi_hi = transfer.pi
    if frp_transfer is not None:
        frp_pi_lo, frp_pi_hi = frp_transfer.pi

    n_h = z.copy()
    n_lo = z.copy()
    n_hi = z.copy()
    frp_h = z.copy()
    frp_lo = z.copy()
    frp_hi = z.copy()
    provenance = np.empty(n_days, dtype=object)

    # MODIS era: month-of-year factors on the comparable subset.
    for i in np.flatnonzero(era_modis):
        m = int(month_of[i])
        terra_only = aqua_start is None or days[i] < aqua_start
        if transfer is None:
            val = float(n_comp_modis[i])
            fval = float(frp_comp_modis[i])
        elif transfer.variant == "per_platform" or terra_only:
            val = moy_factor(transfer.terra, m) * n_terra[i] + \
                (0.0 if terra_only else moy_factor(transfer.aqua, m) * n_aqua[i])
            fval = moy_factor(frp_transfer.terra, m) * frp_terra[i] + \
                (0.0 if terra_only else moy_factor(frp_transfer.aqua, m) * frp_aqua[i]) \
                if frp_transfer is not None else float(frp_comp_modis[i])
        else:
            val = moy_factor(transfer.pooled, m) * n_comp_modis[i]
            fval = (moy_factor(frp_transfer.pooled, m) * frp_comp_modis[i]
                    if frp_transfer is not None else float(frp_comp_modis[i]))
        n_h[i] = val
        n_lo[i] = val * (1.0 + pi_lo)
        n_hi[i] = val * (1.0 + pi_hi)
        frp_h[i] = fval
        frp_lo[i] = fval * (1.0 + frp_pi_lo)
        frp_hi[i] = fval * (1.0 + frp_pi_hi)
        provenance[i] = "MODIS_TRANSFERRED"

    # VIIRS era: reference platform native; continuity after its end.
    snpp_end = snpp_end_ord if snpp_end_ord is not None else (1 << 30)
    use_continuity = era_viirs & (days > snpp_end)
    f_day = float((cont or {}).get("factor_day") or 1.0)
    f_night = float((cont or {}).get("factor_night") or 1.0)
    legacy_lo = float((cont or {}).get("factor_lo") or f_day)
    legacy_hi = float((cont or {}).get("factor_hi") or f_day)
    f_day_lo = float((cont or {}).get("factor_day_lo") or legacy_lo)
    f_day_hi = float((cont or {}).get("factor_day_hi") or legacy_hi)

    n_static_out = z.copy()
    n_night_out = z.copy()
    static_share = np.full(n_days, np.nan)

    native = era_viirs & ~use_continuity
    n_h[native] = n_comp_ref[native]
    n_lo[native] = n_comp_ref[native]
    n_hi[native] = n_comp_ref[native]
    frp_h[native] = frp_comp_ref[native]
    frp_lo[native] = frp_comp_ref[native]
    frp_hi[native] = frp_comp_ref[native]
    n_static_out[native] = n_static_r[native]
    n_night_out[native] = n_night_r[native]
    provenance[native] = "VIIRS_NATIVE"

    # Post-reference era: successor / factor (factor > 1 => successor higher).
    if use_continuity.any() and f_day > 0:
        n_h[use_continuity] = n_comp_suc[use_continuity] / f_day
        n_lo[use_continuity] = n_comp_suc[use_continuity] / f_day_hi
        n_hi[use_continuity] = n_comp_suc[use_continuity] / f_day_lo
        frp_h[use_continuity] = frp_comp_suc[use_continuity] / f_day
        frp_lo[use_continuity] = frp_comp_suc[use_continuity] / f_day_hi
        frp_hi[use_continuity] = frp_comp_suc[use_continuity] / f_day_lo
        n_static_out[use_continuity] = n_static_s[use_continuity] / f_night
        n_night_out[use_continuity] = n_night_s[use_continuity] / f_night
        provenance[use_continuity] = "VIIRS_NATIVE"

    denom = n_static_out + n_night_out + n_h
    share_mask = denom > 0
    static_share[share_mask] = n_static_out[share_mask] / denom[share_mask]

    provenance[no_data] = "NO_DATA"
    era_indicator = np.where(days < viirs_start_ord, 0.0, 1.0)

    # Comparable VIIRS observations actually used by the series: native
    # reference counts in the SNPP era, continuity-divided successor counts
    # after the reference end, 0 before the VIIRS era.
    comp_viirs_used = np.zeros(n_days, dtype=np.float64)
    comp_viirs_used[native] = n_comp_ref[native]
    if use_continuity.any() and f_day > 0:
        comp_viirs_used[use_continuity] = n_comp_suc[use_continuity] / f_day

    return {
        "date_ord": days,
        "n_modis": n_modis_all.astype(np.int64),
        "n_viirs": n_viirs_ref_all.astype(np.int64),
        "n_comparable_modis": n_comp_modis.astype(np.int64),
        "n_comparable_viirs": comp_viirs_used.astype(np.int64),
        "n_harmonized": n_h,
        "n_harmonized_lo": n_lo,
        "n_harmonized_hi": n_hi,
        "frp_modis": per_day_totals(arr, days, is_modis, frp),
        "frp_viirs": per_day_totals(arr, days, is_ref, frp),
        "frp_comparable_modis": frp_comp_modis,
        "frp_comparable_viirs": frp_comp_ref,
        "frp_harmonized": frp_h,
        "frp_harmonized_lo": frp_lo,
        "frp_harmonized_hi": frp_hi,
        "n_static": n_static_out.astype(np.int64),
        "n_night": n_night_out.astype(np.int64),
        "static_share": static_share,
        "era": era_indicator,
        "provenance": provenance,
    }


def overlap_monthly_sums(
    arr: HotspotArrays, start_ord: int, end_ord: int
) -> dict:
    """Aligned monthly sums per sensor over the overlap era -> calibration input."""
    if len(arr) == 0:
        return {"month_keys": [], "m_day": [], "m_night": [], "v_day": [], "v_night": [],
                "m_frp": [], "v_frp": [], "m_frp_day": [], "m_frp_night": [],
                "v_frp_day": [], "v_frp_night": []}
    dates = np.array([date.fromordinal(int(o)) for o in np.unique(arr.date_ord)])
    month_keys = sorted({(d.year, d.month) for d in dates})
    out_m_day = np.zeros(len(month_keys))
    out_m_night = np.zeros(len(month_keys))
    out_v_day = np.zeros(len(month_keys))
    out_v_night = np.zeros(len(month_keys))
    out_m_frp = np.zeros(len(month_keys))
    out_v_frp = np.zeros(len(month_keys))
    out_m_frp_day = np.zeros(len(month_keys))
    out_m_frp_night = np.zeros(len(month_keys))
    out_v_frp_day = np.zeros(len(month_keys))
    out_v_frp_night = np.zeros(len(month_keys))
    index = {k: i for i, k in enumerate(month_keys)}

    d_ord = arr.date_ord
    in_window = (d_ord >= start_ord) & (d_ord <= end_ord)
    months_of_rows = np.empty(len(arr), dtype=np.int64)
    # vectorized month key mapping via lookup of unique date ordinals
    unique_ords = np.unique(d_ord)
    ord_to_month = {}
    for o in unique_ords:
        dd = date.fromordinal(int(o))
        ord_to_month[int(o)] = index.get((dd.year, dd.month), -1)
    lookup = np.array([ord_to_month.get(int(o), -1) for o in range(unique_ords.min(), unique_ords.max() + 1)])
    valid_rows = np.flatnonzero(in_window)
    pos = d_ord[valid_rows] - unique_ords.min()
    months_of_rows[valid_rows] = lookup[np.clip(pos, 0, lookup.size - 1)]

    is_day = arr.daynight == 0
    for mi, key in enumerate(month_keys):
        m_row = valid_rows[(months_of_rows[valid_rows] == mi)]
        if m_row.size == 0:
            continue
        is_m = arr.sensor[m_row] == 0
        is_v = arr.sensor[m_row] == 1
        day_row = is_day[m_row]
        out_m_day[mi] = int(np.count_nonzero(is_m & day_row))
        out_m_night[mi] = int(np.count_nonzero(is_m & ~day_row))
        out_v_day[mi] = int(np.count_nonzero(is_v & day_row))
        out_v_night[mi] = int(np.count_nonzero(is_v & ~day_row))
        frp = arr.frp_mw[m_row]
        finite = np.isfinite(frp)
        out_m_frp[mi] = float(np.sum(frp[is_m & finite]))
        out_v_frp[mi] = float(np.sum(frp[is_v & finite]))
        out_m_frp_day[mi] = float(np.sum(frp[is_m & day_row & finite]))
        out_m_frp_night[mi] = float(np.sum(frp[is_m & ~day_row & finite]))
        out_v_frp_day[mi] = float(np.sum(frp[is_v & day_row & finite]))
        out_v_frp_night[mi] = float(np.sum(frp[is_v & ~day_row & finite]))
    # keep only months with data on both sensors
    both = (out_m_day + out_m_night) > 0
    return {
        "month_keys": [k for k, b in zip(month_keys, both) if b],
        "m_day": out_m_day[both], "m_night": out_m_night[both],
        "v_day": out_v_day[both], "v_night": out_v_night[both],
        "m_frp": out_m_frp[both], "v_frp": out_v_frp[both],
        "m_frp_day": out_m_frp_day[both], "m_frp_night": out_m_frp_night[both],
        "v_frp_day": out_v_frp_day[both], "v_frp_night": out_v_frp_night[both],
    }


def date_range_ords(start: date, end: date) -> tuple[int, int]:
    return start.toordinal(), end.toordinal()


def ordinal_to_date(o: int) -> date:
    return date.fromordinal(int(o))


def shift_date(d: date, days: int) -> date:
    return d + timedelta(days=days)
