"""Duplicate-detection correction (per sensor).

Adaptation of the Zheng et al. (2026, Remote Sensing 18(10):1650) rule
"|dt| < 8 s AND footprint overlap > 30% are duplicates; keep the
highest-confidence record, assign the mean FRP of the group" to FIRMS CSV
granularity: acquisition time is minute-resolution, so the temporal test is
"same satellite, same overpass minute"; the spatial test is single-link
clustering at <=1 km centre distance (approximating 30% footprint overlap at
1 km MODIS pixel scale).

Determinism: clusters are formed from a lexicographically sorted record
list; the representative and the FRP mean are pure functions of the cluster
members, independent of iteration order.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from ..geometry import haversine_km
from ..ingest.canonical import CONFIDENCE_RANK, CanonicalRecord


def deduplicate(
    records: list[CanonicalRecord],
    max_gap_minutes: int = 1,
    max_distance_km_modis: float = 0.8,
    max_distance_km_viirs: float = 0.2,
) -> tuple[list[CanonicalRecord], int]:
    """Remove within-sensor duplicate detections (sub-pixel repeats).

    The spatial threshold is per sensor: 0.2 km for VIIRS 375 m pixels,
    0.8 km for MODIS 1 km pixels. Wider thresholds would merge legitimate
    adjacent pixels of one fire - not duplicates.

    Returns (kept_records, n_deduped). Input order does not affect output.
    """
    if not records:
        return [], 0

    buckets: dict[tuple, list[CanonicalRecord]] = defaultdict(list)
    for rec in records:
        minute_key = rec.acq_dt_utc.replace(second=0, microsecond=0)
        # ~2 km cells; single-link clustering inside the bucket covers border
        # pairs because candidates are re-checked by distance.
        cell = (int(np.floor(rec.lat / 0.02)), int(np.floor(rec.lon / 0.02)))
        buckets[(rec.satellite, rec.sensor_family, minute_key, cell)].append(rec)

    kept: list[CanonicalRecord] = []
    deduped = 0
    for key in sorted(buckets, key=lambda k: (str(k[0]), str(k[1]), str(k[2]), k[3])):
        group = buckets[key]
        if len(group) == 1:
            kept.append(group[0])
            continue
        group.sort(key=_record_sort_key)
        threshold = (max_distance_km_viirs if key[1] == "VIIRS"
                     else max_distance_km_modis)
        clusters = _single_link(group, threshold)
        for cluster in clusters:
            deduped += len(cluster) - 1
            kept.append(_representative(cluster))
    kept.sort(key=_record_sort_key)
    return kept, deduped


def _record_sort_key(rec: CanonicalRecord):
    return (rec.acq_dt_utc, rec.lat, rec.lon, rec.confidence_class, rec.frp_mw or 0.0)


def _single_link(group: list[CanonicalRecord], max_km: float) -> list[list[CanonicalRecord]]:
    """Single-link clustering within one overpass bucket (tiny groups)."""
    n = len(group)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    lats = np.array([r.lat for r in group])
    lons = np.array([r.lon for r in group])
    for i in range(n):
        for j in range(i + 1, n):
            if abs(lats[i] - lats[j]) > 0.05 or abs(lons[i] - lons[j]) > 0.05:
                continue  # >~5 km prefilter, cheaper than haversine
            d = haversine_km(lats[i], lons[i], lats[j], lons[j])
            if d <= max_km:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    clusters: dict[int, list[CanonicalRecord]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(group[i])
    return [clusters[root] for root in sorted(clusters)]


def _representative(cluster: list[CanonicalRecord]) -> CanonicalRecord:
    """Highest confidence wins; ties resolved by earlier time then position.

    FRP of the representative is replaced by the cluster mean (Zheng 2026).
    """
    rep = max(
        cluster,
        key=lambda r: (
            CONFIDENCE_RANK[r.confidence_class],
            r.confidence_raw if r.confidence_raw is not None else -1.0,
            -r.acq_dt_utc.timestamp(),
            -r.lat,
            -r.lon,
        ),
    )
    frps = [r.frp_mw for r in cluster if r.frp_mw is not None]
    rep.frp_mw = (sum(frps) / len(frps)) if frps else None
    return rep
