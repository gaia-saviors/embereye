"""Deterministic fire-episode clustering (Su & Yu 2026 style).

Detections chain into episodes when they are spatially adjacent (<=
`episode_space_km`) and temporally continuous (<= `episode_time_days`
between consecutive detections; chaining through intermediates allowed).

Implementation: day-pair KD-tree linking + union-find.
  * For each pair of days (t, t-1) and (t, t-2), detections of day t are
    linked to detections of the earlier day within `space_km`.
  * Union-find over the resulting edge set is order-independent -> the
    clustering is a pure function of the input (deterministic).
  * Chaining across intermediates emerges naturally (A~B on day 1, B~C on
    day 3 joins A and C in one episode), matching sliding-window tracking.

Episode statistics: span, detection count, peak/total FRP, intensity index
(total FRP x duration, the intensity-duration proxy used to rank "critical
periods"), centroid, bbox, sensor mix.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from ..geometry import local_project


@dataclass(slots=True)
class Episode:
    member_idx: np.ndarray
    start_ord: int
    end_ord: int
    n_detections: int
    peak_frp: float
    total_frp: float
    intensity_index: float
    centroid_lat: float
    centroid_lon: float
    bounds: tuple[float, float, float, float]  # W, S, E, N
    sensor_mix: dict[str, int]


def cluster_episodes(
    arr,  # HotspotArrays
    space_km: float = 5.0,
    time_days: int = 2,
) -> list[Episode]:
    n = len(arr)
    if n == 0:
        return []

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    lat0 = float(np.median(arr.lat))
    px, py = local_project(arr.lat, arr.lon, lat0)
    coords = np.column_stack([px, py])

    # rows grouped by local date
    unique_days = np.unique(arr.date_ord)
    day_rows: dict[int, np.ndarray] = {}
    for d in unique_days:
        day_rows[int(d)] = np.flatnonzero(arr.date_ord == d)

    for day in unique_days:
        rows_t = day_rows[int(day)]
        if rows_t.size == 0:
            continue
        tree_t = cKDTree(coords[rows_t])
        for dt in range(1, time_days + 1):
            rows_p = day_rows.get(int(day) - dt)
            if rows_p is None or rows_p.size == 0:
                continue
            tree_p = cKDTree(coords[rows_p])
            pairs = tree_t.query_ball_tree(tree_p, r=space_km)
            for i, js in enumerate(pairs):
                if not js:
                    continue
                for j in js:
                    union(int(rows_t[i]), int(rows_p[j]))

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)

    episodes = [_build_episode(arr, np.array(sorted(members), dtype=np.int64))
                for members in groups.values()]
    episodes.sort(key=lambda e: (e.start_ord, -e.n_detections, e.centroid_lat))
    return episodes


def _build_episode(arr, members: np.ndarray) -> Episode:
    lats = arr.lat[members]
    lons = arr.lon[members]
    dords = arr.date_ord[members]
    frps = arr.frp_mw[members]
    frp_valid = frps[np.isfinite(frps)]
    peak = float(frp_valid.max()) if frp_valid.size else 0.0
    total = float(frp_valid.sum()) if frp_valid.size else 0.0
    duration = int(dords.max() - dords.min() + 1)
    mix = {"MODIS": int(np.count_nonzero(arr.sensor[members] == 0)),
           "VIIRS": int(np.count_nonzero(arr.sensor[members] == 1))}
    return Episode(
        member_idx=members,
        start_ord=int(dords.min()),
        end_ord=int(dords.max()),
        n_detections=int(members.size),
        peak_frp=peak,
        total_frp=total,
        intensity_index=total * max(1, duration),
        centroid_lat=float(lats.mean()),
        centroid_lon=float(lons.mean()),
        bounds=(float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max())),
        sensor_mix=mix,
    )
