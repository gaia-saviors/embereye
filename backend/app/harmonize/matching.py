"""Near-synchronous cross-sensor footprint pairing.

For each MODIS detection, collect all VIIRS 375 m detections whose centres
fall inside the MODIS footprint (equivalent-area circle from scan/track
pixel geometry, clamped to physical bounds) and whose acquisition time is
within +/-5 minutes - the Aqua <-> S-NPP shared 13:30/01:30 overpass family
(Vadrevu et al. 2018; Zheng et al. 2026 use the same 5-minute window).

Outputs the pairing statistics that feed calibration and the methods panel:
counts of matched footprints, VIIRS FRP sums per MODIS footprint, and the
temporal offset distribution. Deterministic: inputs sorted, matching is a
pure geometric function.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from ..geometry import local_project

MODIS_CODE = 0
VIIRS_CODE = 1
SAT_AQUA = 1
SAT_SNPP = 2

MIN_FOOTPRINT_R_KM = 0.56   # 1 km^2 pixel
MAX_FOOTPRINT_R_KM = 1.75   # 9.6 km^2 scan-edge pixel


@dataclass(slots=True)
class PairStats:
    n_modis_footprints: int = 0
    n_modis_matched: int = 0
    n_viirs_inside: int = 0
    dt_minutes: list[float] = field(default_factory=list)
    # (modis_frp_mw, viirs_frp_sum_mw) for footprints with FRP on both sides
    frp_pairs: list[tuple[float, float]] = field(default_factory=list)
    # matched MODIS footprint index -> sum of contained VIIRS FRP
    viirs_frp_by_modis: dict[int, float] = field(default_factory=dict)
    ratio_per_pair: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        ratios = np.asarray(self.ratio_per_pair, dtype=np.float64)
        out = {
            "modis_footprints_considered": self.n_modis_footprints,
            "modis_footprints_matched": self.n_modis_matched,
            "viirs_detections_inside": self.n_viirs_inside,
            "median_dt_minutes": float(np.median(self.dt_minutes)) if self.dt_minutes else None,
            "frp_pairs": len(self.frp_pairs),
        }
        if ratios.size:
            out["viirs_frp_sum_per_modis_footprint"] = {
                "median": float(np.median(ratios)),
                "p10": float(np.percentile(ratios, 10)),
                "p90": float(np.percentile(ratios, 90)),
            }
        return out


def pair_near_synchronous(
    modis: dict[str, np.ndarray],
    viirs: dict[str, np.ndarray],
    max_dt_minutes: float = 5.0,
) -> PairStats:
    """Pair VIIRS detections into MODIS footprints.

    Arrays are the canonical hotspot fields (see series.hotspot_arrays):
    required keys: t_unix, lat, lon, frp_mw, area_km2, satellite.
    Prefer the Aqua<->S-NPP pair when both present; otherwise use whatever
    the day provides (still time-gated, geometry-gated).
    """
    stats = PairStats()
    m_t, v_t = modis["t_unix"], viirs["t_unix"]
    m_lat, m_lon = modis["lat"], modis["lon"]
    v_lat, v_lon = viirs["lat"], viirs["lon"]
    if m_t.size == 0 or v_t.size == 0:
        return stats

    # Sort for determinism (cKDTree query order is fixed, but sorting pins it).
    m_order = np.lexsort((m_lon, m_lat, m_t))
    v_order = np.lexsort((v_lon, v_lat, v_t))
    m_t, m_lat, m_lon = m_t[m_order], m_lat[m_order], m_lon[m_order]
    m_frp = modis["frp_mw"][m_order]
    m_area = modis["area_km2"][m_order]
    v_t2, v_lat2, v_lon2 = v_t[v_order], v_lat[v_order], v_lon[v_order]
    v_frp = viirs["frp_mw"][v_order]

    lat0 = float(np.median(m_lat))
    mx, my = local_project(m_lat, m_lon, lat0)
    vx, vy = local_project(v_lat, v_lon, lat0)
    v_tree = cKDTree(np.column_stack([vx, vy]))

    radii = np.sqrt(np.maximum(m_area, 0.25) / np.pi)
    radii = np.clip(radii, MIN_FOOTPRINT_R_KM, MAX_FOOTPRINT_R_KM)

    dt_limit_s = max_dt_minutes * 60.0
    stats.n_modis_footprints = int(m_t.size)

    for i in range(m_t.size):
        # time-gated candidate VIIRS slice (both sides are time-sorted)
        lo = np.searchsorted(v_t2, m_t[i] - dt_limit_s)
        hi = np.searchsorted(v_t2, m_t[i] + dt_limit_s, side="right")
        if hi <= lo:
            continue
        idx = np.arange(lo, hi)
        dists, _ = v_tree.query(
            [mx[i], my[i]], k=len(idx), distance_upper_bound=radii[i]
        )
        dists = np.atleast_1d(dists)
        inside = idx[dists <= radii[i]]
        if inside.size == 0:
            continue
        stats.n_modis_matched += 1
        stats.n_viirs_inside += int(inside.size)
        dts = np.abs(v_t2[inside] - m_t[i]) / 60.0
        stats.dt_minutes.extend(float(d) for d in dts)
        v_sum = float(np.nansum(v_frp[inside])) if v_frp[inside].size else 0.0
        stats.viirs_frp_by_modis[i] = v_sum
        m_f = float(m_frp[i]) if np.isfinite(m_frp[i]) else None
        if m_f is not None and m_f > 0 and v_sum > 0:
            stats.frp_pairs.append((m_f, v_sum))
            stats.ratio_per_pair.append(v_sum / m_f)
    return stats
