"""Deterministic geodesic and geometric helpers.

Pure numpy implementations - no GeoPandas, no shapely, no GDAL. Everything
here is a total function with explicit edge cases:
  * haversine distance (km)
  * ray-casting point-in-polygon (GeoJSON lon/lat rings)
  * rectangular bounding boxes
  * fast local Cartesian projection for KD-tree neighbour queries
"""
from __future__ import annotations

import numpy as np

EARTH_RADIUS_KM = 6371.0088


def haversine_km(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Great-circle distance in km, elementwise, arrays broadcast against each other."""
    lat1r = np.radians(np.asarray(lat1, dtype=np.float64))
    lon1r = np.radians(np.asarray(lon1, dtype=np.float64))
    lat2r = np.radians(np.asarray(lat2, dtype=np.float64))
    lon2r = np.radians(np.asarray(lon2, dtype=np.float64))
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def km_to_deg_lat(km: float) -> float:
    return km / 111.195


def km_to_deg_lon(km: float, lat: float) -> float:
    return km / (111.195 * max(np.cos(np.radians(lat)), 1e-6))


def ring_bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a GeoJSON ring."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def points_in_polygon(
    lon: np.ndarray, lat: np.ndarray, ring: list[list[float]]
) -> np.ndarray:
    """Vectorized ray casting against one closed-or-open exterior ring.

    Edge behaviour: points exactly on an edge are treated as inside (inclusive
    boundary test after the crossing test). Handles the antimeridian only in
    the sense that rings must not cross it (validated at AOI creation).
    """
    ring = np.asarray(ring, dtype=np.float64)
    if np.allclose(ring[0], ring[-1]):
        ring = ring[:-1]
    px = np.asarray(lon, dtype=np.float64)
    py = np.asarray(lat, dtype=np.float64)
    inside = np.zeros(px.shape, dtype=bool)
    boundary = np.zeros(px.shape, dtype=bool)
    n = len(ring)
    eps = 1e-12
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        # inclusive boundary test: point on segment
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        within = (
            (np.minimum(x1, x2) - eps <= px) & (px <= np.maximum(x1, x2) + eps)
            & (np.minimum(y1, y2) - eps <= py) & (py <= np.maximum(y1, y2) + eps)
        )
        boundary |= (np.abs(cross) <= eps * 1e6) & within
        crosses = (y1 > py) != (y2 > py)
        if not crosses.any():
            continue
        x_int = (x2 - x1) * (py - y1) / (y2 - y1 + 1e-300) + x1
        inside ^= crosses & (px < x_int)
    return inside | boundary


def local_project(lat: np.ndarray, lon: np.ndarray, lat0: float) -> tuple[np.ndarray, np.ndarray]:
    """Equirectangular projection to km around reference latitude `lat0`.

    Accurate over the few-hundred-km scales used by pairing and clustering;
    deterministic and allocation-cheap.
    """
    x = np.asarray(lon, dtype=np.float64) * 111.195 * np.cos(np.radians(lat0))
    y = np.asarray(lat, dtype=np.float64) * 111.195
    return x, y


def polygon_centroid(ring: list[list[float]]) -> tuple[float, float]:
    """Area-weighted centroid of a ring (shoelace). Falls back to vertex mean."""
    r = np.asarray(ring, dtype=np.float64)
    if np.allclose(r[0], r[-1]):
        r = r[:-1]
    x, y = r[:, 0], r[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    area = cross.sum() / 2.0
    if abs(area) < 1e-12:
        return float(x.mean()), float(y.mean())
    cx = ((x + xn) * cross).sum() / (6.0 * area)
    cy = ((y + yn) * cross).sum() / (6.0 * area)
    return float(cx), float(cy)
