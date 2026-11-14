"""Hotspot vector access (GeoJSON) with server-side filtering.

Preset AOIs filter on the indexed aoi_id; user AOIs use the lat/lon bbox
prefilter plus exact point-in-polygon. Pagination is explicit and honest:
`meta.total` reports the true filtered count (capped scan for user AOIs is
reported via `meta.truncated`).

v1.0.6 (handoff P1): the map timeline no longer pulls per-tick GeoJSON.
Two new endpoints carry the playback workload:

  GET /hotspots/snapshot  one compact COLUMNAR binary payload per (AOI),
      gzip + ETag: rows sorted by day, quantised lat/lon, satellite uint8,
      FRP x10, packed flags. The client slices any playback day in memory
      (O(1) per-day offsets) - scrubbing performs ZERO network requests.
      Above SNAPSHOT_MAX_ROWS the endpoint answers with a JSON body
      (mode="grid") telling the client to use the aggregated path.

  GET /hotspots/grid      server-side aggregation (grid cell x week|month)
      for the overview of large AOIs; raw points are then fetched per
      viewport bbox + day window through the regular /hotspots endpoint
      (new `bbox` param) under the hard row cap - never a 200k-row dump.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import struct
from datetime import date, timedelta
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Integer, cast as sa_cast, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..geometry import points_in_polygon
from ..models import Aoi, Hotspot
from ..schemas import CollectionMeta, HotspotCollection

router = APIRouter()

DateParam = date | None
HARD_SCAN_CAP = 200_000
# Snapshot cap: at ~21 bytes/row a 400k-row AOI is ~8.4 MB raw / ~3-4 MB
# gzipped - acceptable as a ONE-TIME load per AOI. Anything bigger falls
# back to the grid (aggregated) path.
SNAPSHOT_MAX_ROWS = 400_000
DAY0 = date(2000, 1, 1).toordinal()

FLAG_NIGHT = 1 << 0
FLAG_STATIC = 1 << 1
FLAG_SATURATED = 1 << 2
FLAG_BAND21 = 1 << 3
PRODUCT_NONE, PRODUCT_SP, PRODUCT_NRT = 0, 1, 2
CONF_LOW, CONF_NOMINAL, CONF_HIGH, CONF_UNKNOWN = 0, 1, 2, 3

SAT_ORDER = ["Terra", "Aqua", "SNPP", "N20", "N21"]


def _aoi_or_404(db: Session, aoi_id: str) -> Aoi:
    aoi = db.get(Aoi, aoi_id)
    if aoi is None:
        raise HTTPException(404, "AOI not found")
    return aoi


def _base_filters(aoi: Aoi):
    from datetime import date as _date

    if aoi.is_preset:
        return [Hotspot.aoi_id == aoi.id]
    return [
        Hotspot.lat >= aoi.south, Hotspot.lat <= aoi.north,
        Hotspot.lon >= aoi.west, Hotspot.lon <= aoi.east,
    ]


def _apply_common(stmt, aoi: Aoi, start, end, sensor, satellite, confidence,
                   daynight, min_frp):
    for f in _base_filters(aoi):
        stmt = stmt.where(f)
    if start is not None:
        stmt = stmt.where(Hotspot.local_date >= start)
    if end is not None:
        stmt = stmt.where(Hotspot.local_date <= end)
    if sensor and sensor != "all":
        stmt = stmt.where(Hotspot.sensor_family == sensor)
    if satellite:
        stmt = stmt.where(Hotspot.satellite == satellite)
    if confidence and confidence != "all":
        stmt = stmt.where(Hotspot.confidence_class == confidence)
    if daynight and daynight != "all":
        stmt = stmt.where(Hotspot.daynight == daynight)
    if min_frp is not None:
        stmt = stmt.where(Hotspot.frp_mw >= min_frp)
    return stmt


def _pip_filter(rows: list, aoi: Aoi) -> list:
    if aoi.is_preset or not rows or getattr(aoi, "bbox_only", False):
        return rows
    ring = json.loads(aoi.geojson)["coordinates"][0]
    lats = [r.lat for r in rows]
    lons = [r.lon for r in rows]
    import numpy as np

    inside = points_in_polygon(np.asarray(lons), np.asarray(lats), ring)
    return [r for r, keep in zip(rows, inside) if keep]


def _exact_filtered_count(db: Session, aoi: Aoi, sensor, satellite, confidence,
                          daynight, min_frp) -> int:
    """Count the selected population, applying the custom polygon exactly."""
    if aoi.is_preset:
        stmt = _apply_common(
            select(func.count()).select_from(Hotspot), aoi, None, None,
            sensor, satellite, confidence, daynight, min_frp,
        )
        return int(db.scalar(stmt) or 0)

    stmt = _apply_common(
        select(Hotspot.lon, Hotspot.lat), aoi, None, None,
        sensor, satellite, confidence, daynight, min_frp,
    )
    from ..geometry import points_in_polygon
    ring = json.loads(aoi.geojson)["coordinates"][0]
    result = db.execute(stmt)
    total = 0
    while batch := result.fetchmany(50_000):
        import numpy as np

        inside = points_in_polygon(
            np.asarray([r[0] for r in batch]),
            np.asarray([r[1] for r in batch]), ring,
        )
        total += int(inside.sum())
    return total


@router.get("/hotspots", response_model=HotspotCollection)
def hotspots(
    aoi: str = Query(..., description="AOI id"),
    start: DateParam = None,
    end: DateParam = None,
    sensor: str = Query("all", pattern="^(all|MODIS|VIIRS)$"),
    satellite: str | None = Query(None),
    confidence: str = Query("all", pattern="^(all|LOW|NOMINAL|HIGH)$"),
    daynight: str = Query("all", pattern="^(all|D|N)$"),
    min_frp: float | None = Query(None, ge=0),
    hide_static: bool = Query(False),
    limit: int = Query(5000, ge=1, le=20000),
    offset: int = Query(0, ge=0),
    # v1.0.6: viewport-bounded raw fetch for the grid path (zoomed-in map
    # playback on large AOIs). w,s,e,n in degrees; overrides the AOI
    # spatial filter - the AOI is still required and must exist.
    bbox: str | None = Query(None,
        description='viewport bounds "w,s,e,n" in degrees'),
    response: Response = None,
    db: Session = Depends(get_db),
) -> HotspotCollection:
    a = _aoi_or_404(db, aoi)
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "start must be on or before end")
    viewport = None
    if bbox is not None:
        try:
            bw, bs, be, bn = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(422, "bbox must be 'w,s,e,n' in degrees")
        if not (-180 <= bw < be <= 180 and -90 <= bs < bn <= 90):
            raise HTTPException(422, "bbox out of range or inverted")
        viewport = (bw, bs, be, bn)
    total_stmt = _apply_common(
        select(func.count()).select_from(Hotspot), a, start, end, sensor,
        satellite, confidence, daynight, min_frp,
    )
    if viewport is not None:
        bw, bs, be, bn = viewport
        total_stmt = total_stmt.where(
            Hotspot.lon >= bw, Hotspot.lon <= be,
            Hotspot.lat >= bs, Hotspot.lat <= bn,
        )
    if hide_static:
        total_stmt = total_stmt.where(Hotspot.static_source.is_(False))
    total = int(db.scalar(total_stmt) or 0)

    stmt = _apply_common(select(Hotspot), a, start, end, sensor, satellite,
                         confidence, daynight, min_frp)
    if viewport is not None:
        bw, bs, be, bn = viewport
        stmt = stmt.where(
            Hotspot.lon >= bw, Hotspot.lon <= be,
            Hotspot.lat >= bs, Hotspot.lat <= bn,
        )
    if hide_static:
        stmt = stmt.where(Hotspot.static_source.is_(False))
    if a.is_preset:
        stmt = stmt.order_by(Hotspot.local_date, Hotspot.id).offset(offset).limit(limit)
        window = db.execute(stmt).scalars().all()
        total_pip = total
        scan_truncated = False
    else:
        # Read one extra candidate to distinguish a complete scan from the
        # safety cap. The polygon is applied before pagination and counting.
        stmt = stmt.order_by(Hotspot.local_date, Hotspot.id).limit(HARD_SCAN_CAP + 1)
        candidates = list(db.execute(stmt).scalars().all())
        scan_truncated = len(candidates) > HARD_SCAN_CAP
        rows = _pip_filter(candidates[:HARD_SCAN_CAP], a)
        total_pip = len(rows)
        window = rows[offset:offset + limit]

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
            "properties": {
                "id": r.id, "sensor": r.sensor_family, "satellite": r.satellite,
                "acq_dt_utc": r.acq_dt_utc.isoformat(), "local_date": r.local_date.isoformat(),
                "frp_mw": r.frp_mw, "t4_k": r.brightness_t4_k, "t11_k": r.brightness_t11_k,
                "confidence": r.confidence_class, "daynight": r.daynight,
                "pixel_area_km2": r.pixel_area_km2, "saturated": r.saturated,
                "band21_fallback": r.band21_fallback, "static_source": r.static_source,
                "firms_product": r.firms_product or None, "firms_type": r.firms_type,
            },
        }
        for r in window
    ]
    truncated = scan_truncated or total_pip > offset + len(features)
    if response is not None:
        response.headers["X-Result-Truncated"] = str(truncated).lower()
        response.headers["X-Result-Total"] = str(total_pip)
        response.headers["X-Result-Limit"] = str(limit)
        response.headers["X-Result-Total-Exact"] = str(not scan_truncated).lower()
    return HotspotCollection(
        features=features,
        meta=CollectionMeta(
            total=total_pip, returned=len(features), offset=offset,
            truncated=truncated,
            total_is_lower_bound=scan_truncated,
        ),
    )


# ---------------------------------------------------------------------------
# v1.0.6 columnar snapshot (P1b): one compact payload per (AOI, attribute
# filters). Rows are sorted by local_date so the client can build an O(1)
# per-day offset index and slice any playback window in memory - scrubbing
# performs ZERO network requests.
# ---------------------------------------------------------------------------

SNAP_MAGIC = b"EMBSNAP1"
# day u2, lat i4, lon i4, acq_day u2, acq_min u2, sat u1, frp u2, t4 i2,
# conf u1, flags u1  -> 21 bytes  ("<HiiHHBHhBB", no padding)
SNAP_STRIDE = 21
SNAP_FIELDS = [
    "day:u16", "lat:i32e5", "lon:i32e5", "acq_day:u16", "acq_min:u16",
    "sat:u8", "frp:u16x10", "t4:i16x10off200", "conf:u8", "flags:u8",
]

# Process-level cache: fingerprint -> (etag, gzip payload). The fingerprint
# is (attribute filters + row COUNT + MAX(id)) of the filtered set - a
# single indexed query, so a 304 revalidation costs milliseconds instead of
# a full re-encode. Bounded: at most _SNAP_CACHE_MAX payloads held.
_SNAP_CACHE: dict[tuple, tuple[str, bytes, int]] = {}
_SNAP_CACHE_MAX = 4


def _snapshot_fingerprint(db: Session, a: Aoi, sensor, satellite, confidence,
                          daynight, min_frp) -> tuple:
    # COUNT + MAX(id) + SUM(static_source) + MAX(local_date): catches inserts
    # AND the reclassify_static.py workflow (static flag flips without new
    # ids). One indexed aggregate query - milliseconds.
    fp_stmt = _apply_common(
        select(func.count(), func.max(Hotspot.id),
               func.coalesce(func.sum(func.cast(Hotspot.static_source, Integer)), 0),
               func.max(Hotspot.local_date)), a, None, None,
        sensor, satellite, confidence, daynight, min_frp,
    )
    row = db.execute(fp_stmt).one()
    count = (int(row[0] or 0) if a.is_preset else
             _exact_filtered_count(db, a, sensor, satellite, confidence,
                                   daynight, min_frp))
    return (a.id, sensor, satellite or "", confidence, daynight,
            min_frp if min_frp is not None else "", count,
            int(row[1] or 0), int(row[2] or 0), str(row[3]))


def _encode_snapshot(rows: list) -> tuple[bytes, dict]:
    """Encode hotspot rows (sorted by local_date) into the snapshot binary
    format. Accepts both ORM objects and plain tuples (the column-tuple
    path below avoids instantiating 170k+ ORM objects). Returns
    (gzip_bytes, header_dict)."""
    n = len(rows)
    buf = bytearray(n * SNAP_STRIDE)
    off = 0
    day_min = day_max = None
    first = rows[0] if rows else None
    tuple_mode = not hasattr(first, "local_date")
    for r in rows:
        if tuple_mode:
            (loc_date, acq_date, acq_dt, lat, lon, sat_name, frp_mw, t4_k,
             conf_name, dn, static, saturated, band21, product) = r
        else:
            loc_date = r.local_date
            acq_date = r.acq_date
            acq_dt = r.acq_dt_utc
            lat, lon = r.lat, r.lon
            sat_name, frp_mw, t4_k = r.satellite, r.frp_mw, r.brightness_t4_k
            conf_name, dn = r.confidence_class, r.daynight
            static, saturated, band21 = (r.static_source, r.saturated,
                                         r.band21_fallback)
            product = r.firms_product
        doff = loc_date.toordinal() - DAY0
        if day_min is None:
            day_min = loc_date
        day_max = loc_date
        acq_day = acq_date.toordinal() - DAY0
        acq_min = acq_dt.hour * 60 + acq_dt.minute
        try:
            sat = SAT_ORDER.index(sat_name)
        except ValueError:
            sat = 255
        frp = (65535 if frp_mw is None
               else min(65535, max(0, int(round(frp_mw * 10)))))
        t4 = (-32768 if t4_k is None
              else max(-32767, min(32767, int(round((t4_k - 200.0) * 10)))))
        conf = {"LOW": CONF_LOW, "NOMINAL": CONF_NOMINAL, "HIGH": CONF_HIGH}.get(
            conf_name, CONF_UNKNOWN)
        flags = 0
        if dn == "N":
            flags |= FLAG_NIGHT
        if static:
            flags |= FLAG_STATIC
        if saturated:
            flags |= FLAG_SATURATED
        if band21:
            flags |= FLAG_BAND21
        if product == "SP":
            flags |= 1 << 4
        elif product == "NRT":
            flags |= 1 << 5
        struct.pack_into(
            "<HiiHHBHhBB", buf, off,
            doff, int(round(lat * 1e5)), int(round(lon * 1e5)),
            acq_day, acq_min, sat, frp, t4, conf, flags,
        )
        off += SNAP_STRIDE
    header = {
        "mode": "snapshot",
        "n": n,
        "stride": SNAP_STRIDE,
        "fields": SNAP_FIELDS,
        "day0": date(2000, 1, 1).isoformat(),
        "day_min": day_min.isoformat() if day_min else None,
        "day_max": day_max.isoformat() if day_max else None,
        "sat_order": SAT_ORDER,
    }
    header_json = json.dumps(header).encode()
    raw = SNAP_MAGIC + len(header_json).to_bytes(4, "little") + header_json + bytes(buf)
    # mtime=0: the gzip header must not carry wall-clock time, otherwise the
    # ETag (sha over the payload) changes on every request and 304 revalidation
    # never hits.
    return gzip.compress(raw, 6, mtime=0), header


@router.get("/hotspots/snapshot")
def hotspots_snapshot(
    request: Request,
    aoi: str = Query(...),
    sensor: str = Query("all", pattern="^(all|MODIS|VIIRS)$"),
    satellite: str | None = Query(None),
    confidence: str = Query("all", pattern="^(all|LOW|NOMINAL|HIGH)$"),
    daynight: str = Query("all", pattern="^(all|D|N)$"),
    min_frp: float | None = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    """Full-record columnar snapshot for one (AOI, attribute filters).

    Binary layout (after gunzip): EMBSNAP1 + u32 header length + UTF-8 JSON
    header + n fixed 21-byte records sorted by day. The response carries a
    strong ETag and Cache-Control; If-None-Match is answered with 304, so a
    reload costs one revalidation, not one payload.

    Attribute filters (sensor/confidence/daynight/min_frp/satellite) are part
    of the cache key; the static-source and NRT-provisional toggles stay
    client-side flags (they are in the flags byte) so switching them never
    refetches. Date windows are client-side slices by construction.
    """
    a = _aoi_or_404(db, aoi)
    # Cheap fingerprint first: a filtered COUNT+MAX(id) is an indexed scan,
    # so both the cap check and the ETag cache key cost milliseconds.
    fp = _snapshot_fingerprint(db, a, sensor, satellite, confidence,
                               daynight, min_frp)
    total = fp[6]
    if total > SNAPSHOT_MAX_ROWS:
        # Too big for a one-time columnar payload: hand the client to the
        # aggregated grid path (JSON 200, not an error).
        return JSONResponse({
            "mode": "grid",
            "aoi": aoi,
            "n_rows": total,
            "max_rows": SNAPSHOT_MAX_ROWS,
            "truncated": True,
            "total_is_lower_bound": False,
            "detail": ("AOI holds more rows than the snapshot cap; use "
                       "/hotspots/grid for the overview and /hotspots with "
                       "a bbox + day window for zoomed-in playback."),
        }, headers={
            "X-Result-Truncated": "true",
            "X-Result-Total": str(total),
            "X-Result-Limit": str(SNAPSHOT_MAX_ROWS),
            "X-Result-Total-Exact": "true",
        })
    cached = _SNAP_CACHE.get(fp)
    if cached is not None:
        etag, payload, n_rows = cached
        headers = {
            "ETag": etag,
            "Cache-Control": "public, max-age=300, must-revalidate",
            "X-Snapshot-Rows": str(n_rows),
        }
        inm = request.headers.get("if-none-match")
        if inm and inm == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=payload,
                        media_type="application/octet-stream", headers=headers)
    stmt = _apply_common(
        select(
            Hotspot.local_date, Hotspot.acq_date, Hotspot.acq_dt_utc,
            Hotspot.lat, Hotspot.lon, Hotspot.satellite, Hotspot.frp_mw,
            Hotspot.brightness_t4_k, Hotspot.confidence_class, Hotspot.daynight,
            Hotspot.static_source, Hotspot.saturated, Hotspot.band21_fallback,
            Hotspot.firms_product,
        ), a, None, None, sensor, satellite, confidence, daynight, min_frp)
    stmt = stmt.order_by(Hotspot.local_date, Hotspot.id)
    # column tuples, not ORM objects: 170k+ identities are the encode
    # bottleneck otherwise
    result = db.execute(stmt)
    if a.is_preset:
        rows = list(result.all())
    else:
        from ..geometry import points_in_polygon
        import numpy as np

        ring = json.loads(a.geojson)["coordinates"][0]
        rows = []
        while batch := result.fetchmany(50_000):
            inside = points_in_polygon(
                np.asarray([r[4] for r in batch]),
                np.asarray([r[3] for r in batch]), ring,
            )
            rows.extend(r for r, keep in zip(batch, inside) if keep)
            if len(rows) > SNAPSHOT_MAX_ROWS:
                break
    payload, header = _encode_snapshot(rows)
    etag = '"' + hashlib.sha256(payload).hexdigest()[:32] + '"'
    if len(_SNAP_CACHE) >= _SNAP_CACHE_MAX:
        _SNAP_CACHE.clear()
    _SNAP_CACHE[fp] = (etag, payload, header["n"])
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=300, must-revalidate",
        "X-Snapshot-Rows": str(header["n"]),
    }
    inm = request.headers.get("if-none-match")
    if inm and inm == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=payload, media_type="application/octet-stream",
                    headers=headers)


# ---------------------------------------------------------------------------
# v1.0.6 server-side grid aggregation (P1c): overview for AOIs too large for
# the snapshot path. Grid cell x week|month buckets, computed IN THE DATABASE.
# ---------------------------------------------------------------------------

GRID_MAX_CELLS = 20_000
GRID_DEFAULT_CELL_DEG = 0.25


def _bucket_expr(dialect: str, col):
    """ISO-week (Monday-anchored) or calendar-month bucket for local_date."""
    if dialect == "sqlite":
        return {
            "month": func.strftime("%Y-%m", col),
            "week": func.date(col, "-6 days", "weekday 1"),
        }
    # Postgres / others
    return {
        "month": func.to_char(col, "YYYY-MM"),
        "week": func.date_trunc("week", col),
    }


@router.get("/hotspots/grid")
def hotspots_grid(
    aoi: str = Query(...),
    res: str = Query("week", pattern="^(week|month)$"),
    start: DateParam = None,
    end: DateParam = None,
    cell_deg: float = Query(GRID_DEFAULT_CELL_DEG, gt=0.01, le=5.0),
    sensor: str = Query("all", pattern="^(all|MODIS|VIIRS)$"),
    satellite: str | None = Query(None),
    confidence: str = Query("all", pattern="^(all|LOW|NOMINAL|HIGH)$"),
    daynight: str = Query("all", pattern="^(all|D|N)$"),
    min_frp: float | None = Query(None, ge=0),
    hide_static: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Aggregated overview: grid cells x week|month, aggregated server-side.

    Never returns raw points - the client draws cell rectangles and fetches
    raw rows ONLY for the zoomed-in viewport through /hotspots?bbox=... under
    the hard row cap, with "showing N of M" honesty.
    """
    a = _aoi_or_404(db, aoi)
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "start must be on or before end")
    dialect = db.get_bind().dialect.name
    w0 = float(int(a.west / cell_deg)) * cell_deg
    s0 = float(int(a.south / cell_deg)) * cell_deg
    gx = sa_cast((Hotspot.lon - w0) / cell_deg, Integer)
    gy = sa_cast((Hotspot.lat - s0) / cell_deg, Integer)
    bucket = _bucket_expr(dialect, Hotspot.local_date)[res]

    conds = list(_base_filters(a))
    if start is not None:
        conds.append(Hotspot.local_date >= start)
    if end is not None:
        conds.append(Hotspot.local_date <= end)
    if sensor and sensor != "all":
        conds.append(Hotspot.sensor_family == sensor)
    if satellite:
        conds.append(Hotspot.satellite == satellite)
    if confidence and confidence != "all":
        conds.append(Hotspot.confidence_class == confidence)
    if daynight and daynight != "all":
        conds.append(Hotspot.daynight == daynight)
    if min_frp is not None:
        conds.append(Hotspot.frp_mw >= min_frp)
    if hide_static:
        conds.append(Hotspot.static_source.is_(False))

    if a.is_preset:
        rows = db.execute(
            select(
                gx.label("gx"), gy.label("gy"), bucket.label("bkt"),
                func.count().label("n"),
                func.sum(sa_cast(Hotspot.sensor_family == "MODIS", Integer)).label("n_modis"),
                func.sum(sa_cast(Hotspot.sensor_family == "VIIRS", Integer)).label("n_viirs"),
                func.coalesce(func.sum(Hotspot.frp_mw), 0.0).label("frp"),
                func.sum(sa_cast(Hotspot.static_source.is_(True), Integer)).label("n_static"),
            )
            .where(*conds)
            .group_by(gx, gy, bucket)
            .order_by(func.count().desc())
            .limit(GRID_MAX_CELLS + 1)
        ).all()
        total_points = int(db.scalar(
            select(func.count()).select_from(Hotspot).where(*conds)) or 0)
    else:
        # A grouped SQL bbox result includes points outside a drawn polygon.
        # Filter candidate rows exactly, then aggregate only accepted points.
        from ..geometry import points_in_polygon
        import numpy as np

        ring = json.loads(a.geojson)["coordinates"][0]
        grouped: dict[tuple[int, int, str], dict] = {}
        result = db.execute(select(
            Hotspot.lon, Hotspot.lat, Hotspot.local_date,
            Hotspot.sensor_family, Hotspot.frp_mw, Hotspot.static_source,
        ).where(*conds))
        total_points = 0
        while batch := result.fetchmany(50_000):
            inside = points_in_polygon(
                np.asarray([r[0] for r in batch]),
                np.asarray([r[1] for r in batch]), ring,
            )
            for r, keep in zip(batch, inside):
                if not keep:
                    continue
                lon, lat, day, family, frp, static = r
                ix = int((lon - w0) / cell_deg)
                iy = int((lat - s0) / cell_deg)
                if res == "month":
                    bkt = day.strftime("%Y-%m")
                else:
                    bkt = (day - timedelta(days=day.weekday())).isoformat()
                item = grouped.setdefault((ix, iy, bkt), {
                    "gx": ix, "gy": iy, "bkt": bkt, "n": 0,
                    "n_modis": 0, "n_viirs": 0, "frp": 0.0, "n_static": 0,
                })
                item["n"] += 1
                item["n_modis"] += int(family == "MODIS")
                item["n_viirs"] += int(family == "VIIRS")
                item["frp"] += float(frp or 0.0)
                item["n_static"] += int(bool(static))
                total_points += 1
        rows = sorted(grouped.values(), key=lambda r: (-r["n"], r["bkt"], r["gy"], r["gx"]))

    truncated = len(rows) > GRID_MAX_CELLS
    rows = rows[:GRID_MAX_CELLS]
    cells = [
        {
            "i": int(r["gx"] if isinstance(r, dict) else r.gx),
            "j": int(r["gy"] if isinstance(r, dict) else r.gy),
            "w": round(w0 + int(r["gx"] if isinstance(r, dict) else r.gx) * cell_deg, 5),
            "s": round(s0 + int(r["gy"] if isinstance(r, dict) else r.gy) * cell_deg, 5),
            "e": round(w0 + (int(r["gx"] if isinstance(r, dict) else r.gx) + 1) * cell_deg, 5),
            "n_": round(s0 + (int(r["gy"] if isinstance(r, dict) else r.gy) + 1) * cell_deg, 5),
            "bucket": str(r["bkt"] if isinstance(r, dict) else r.bkt),
            "n": int(r["n"] if isinstance(r, dict) else r.n),
            "n_modis": int((r["n_modis"] if isinstance(r, dict) else r.n_modis) or 0),
            "n_viirs": int((r["n_viirs"] if isinstance(r, dict) else r.n_viirs) or 0),
            "n_static": int((r["n_static"] if isinstance(r, dict) else r.n_static) or 0),
            "frp_mw": round(float((r["frp"] if isinstance(r, dict) else r.frp) or 0.0), 1),
        }
        for r in rows
    ]
    return JSONResponse({
        "mode": "grid", "aoi": a.id, "res": res, "cell_deg": cell_deg,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "cells": cells,
        "meta": {
            "total_points": total_points, "n_cells": len(cells),
            "truncated": truncated,
            "max_cells": GRID_MAX_CELLS,
        },
    })
