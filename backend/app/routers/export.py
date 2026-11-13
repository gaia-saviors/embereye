"""CSV / GeoJSON export endpoints (streamed, no in-memory blowups)."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Aoi, DailyAggregate, FireEpisode, HarmonizedDaily, Hotspot
from ..routers.hotspots import _apply_common
from ..schemas import ExportFormat, ExportLevel

router = APIRouter()

CHUNK = 5000


def _csv_response(filename: str, header: list[str], rows: Iterator[list]) -> StreamingResponse:
    def gen() -> Iterator[bytes]:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(header)
        yield buf.getvalue().encode("utf-8")
        count = 0
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for row in rows:
            writer.writerow(row)
            count += 1
            if count % CHUNK == 0:
                yield buf.getvalue().encode("utf-8")
                buf = io.StringIO()
                writer = csv.writer(buf, lineterminator="\n")
        yield buf.getvalue().encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _geojson_response(filename: str, features: Iterator[dict]) -> StreamingResponse:
    def gen() -> Iterator[bytes]:
        yield b'{"type":"FeatureCollection","features":[\n'
        first = True
        batch: list[str] = []
        for feature in features:
            batch.append(json.dumps(feature, separators=(",", ":")))
            if len(batch) >= 1500:
                prefix = b"" if first else b",\n"
                yield prefix + (",\n".join(batch)).encode("utf-8")
                batch = []
                first = False
        if batch:
            prefix = b"" if first else b",\n"
            yield prefix + (",\n".join(batch)).encode("utf-8")
        yield b"\n]}"

    return StreamingResponse(
        gen(),
        media_type="application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _stream_geojson_hotspots(
    stmt, is_preset: bool, geojson_str: str | None = None,
    bbox_only: bool = False, chunk_size: int = 1500,
) -> Iterator[bytes]:
    db = SessionLocal()
    try:
        cursor = db.execute(stmt)
        yield b'{"type":"FeatureCollection","features":[\n'
        first = True
        batch: list[str] = []

        ring = None
        if not is_preset and not bbox_only and geojson_str:
            ring = json.loads(geojson_str)["coordinates"][0]
            from ..geometry import points_in_polygon
            import numpy as np

        for r in cursor:
            lon = round(float(r[0]), 5)
            lat = round(float(r[1]), 5)
            if ring is not None:
                if not points_in_polygon(np.array([lon]), np.array([lat]), ring)[0]:
                    continue

            dt_str = r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4])
            d_str = r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5])
            feat = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "sensor": r[2],
                    "satellite": r[3],
                    "acq_dt_utc": dt_str,
                    "local_date": d_str,
                    "frp_mw": round(float(r[6]), 2) if r[6] is not None else 0.0,
                    "t4_k": round(float(r[7]), 1) if r[7] is not None else None,
                    "t11_k": round(float(r[8]), 1) if r[8] is not None else None,
                    "confidence": r[9],
                    "daynight": r[10],
                    "pixel_area_km2": round(float(r[11]), 4) if r[11] is not None else None,
                    "saturated": bool(r[12]),
                    "band21_fallback": bool(r[13]),
                    "static_source": bool(r[14]),
                },
            }
            batch.append(json.dumps(feat, separators=(",", ":")))
            if len(batch) >= chunk_size:
                prefix = b"" if first else b",\n"
                yield prefix + (",\n".join(batch)).encode("utf-8")
                batch = []
                first = False
        if batch:
            prefix = b"" if first else b",\n"
            yield prefix + (",\n".join(batch)).encode("utf-8")
        yield b"\n]}"
    finally:
        db.close()


def _stream_csv_hotspots(
    stmt, is_preset: bool, geojson_str: str | None = None,
    bbox_only: bool = False, chunk_size: int = 4000,
) -> Iterator[bytes]:
    db = SessionLocal()
    try:
        cursor = db.execute(stmt)
        header = (
            "local_date,acq_dt_utc,sensor,satellite,resolution_m,lat,lon,frp_mw,"
            "t4_k,t11_k,confidence,daynight,scan_km,track_km,pixel_area_km2,saturated,"
            "band21_fallback,static_source,source_version\n"
        )
        yield header.encode("utf-8")
        batch: list[str] = []

        ring = None
        if not is_preset and not bbox_only and geojson_str:
            ring = json.loads(geojson_str)["coordinates"][0]
            from ..geometry import points_in_polygon
            import numpy as np

        for r in cursor:
            lon = round(float(r[0]), 5)
            lat = round(float(r[1]), 5)
            if ring is not None:
                if not points_in_polygon(np.array([lon]), np.array([lat]), ring)[0]:
                    continue

            dt_str = r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4])
            d_str = r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5])
            t4_str = f"{float(r[7]):.1f}" if r[7] is not None else ""
            t11_str = f"{float(r[8]):.1f}" if r[8] is not None else ""
            pa_str = f"{float(r[11]):.4f}" if r[11] is not None else ""
            scan_str = f"{float(r[16]):.2f}" if r[16] is not None else ""
            track_str = f"{float(r[17]):.2f}" if r[17] is not None else ""
            row_str = (
                f"{d_str},{dt_str},{r[2]},{r[3]},{r[15]},"
                f"{lat:.5f},{lon:.5f},{float(r[6]):.2f},{t4_str},{t11_str},"
                f"{r[9]},{r[10]},{scan_str},{track_str},"
                f"{pa_str},{r[12]},{r[13]},{r[14]},{r[18] or ''}"
            )
            batch.append(row_str)
            if len(batch) >= chunk_size:
                yield ("\n".join(batch) + "\n").encode("utf-8")
                batch = []
        if batch:
            yield ("\n".join(batch) + "\n").encode("utf-8")
    finally:
        db.close()


@router.get("/export")
def export(
    aoi: str = Query(...),
    level: ExportLevel = Query("hotspots"),
    format: ExportFormat = Query("csv"),
    start: date | None = None,
    start_date: date | None = None,
    end: date | None = None,
    end_date: date | None = None,
    sensor: str = Query("all", pattern="^(all|MODIS|VIIRS)$"),
    satellite: str | None = Query(None),
    confidence: str = Query("all", pattern="^(all|LOW|NOMINAL|HIGH)$"),
    daynight: str = Query("all", pattern="^(all|D|N)$"),
    min_frp: float | None = None,
    hide_static: bool = False,
    db: Session = Depends(get_db),
):
    a = db.get(Aoi, aoi)
    if a is None:
        raise HTTPException(404, "AOI not found")

    eff_start = start or start_date
    eff_end = end or end_date
    fname = f"embereye_{aoi}_{level}_{eff_start or 'start'}_{eff_end or 'end'}.{format}".replace(":", "")

    if level == "hotspots":
        cols = [
            Hotspot.lon, Hotspot.lat, Hotspot.sensor_family, Hotspot.satellite,
            Hotspot.acq_dt_utc, Hotspot.local_date, Hotspot.frp_mw,
            Hotspot.brightness_t4_k, Hotspot.brightness_t11_k, Hotspot.confidence_class,
            Hotspot.daynight, Hotspot.pixel_area_km2, Hotspot.saturated,
            Hotspot.band21_fallback, Hotspot.static_source, Hotspot.resolution_m,
            Hotspot.scan_km, Hotspot.track_km, Hotspot.source_version
        ]
        stmt = select(*cols)
        stmt = _apply_common(stmt, a, eff_start, eff_end, sensor, satellite, confidence, daynight, min_frp)
        if hide_static:
            stmt = stmt.where(Hotspot.static_source.is_(False))
        stmt = stmt.order_by(Hotspot.local_date, Hotspot.id).limit(500_000)

        if format == "geojson":
            return StreamingResponse(
                _stream_geojson_hotspots(stmt, a.is_preset, a.geojson, getattr(a, "bbox_only", False)),
                media_type="application/geo+json",
                headers={"Content-Disposition": f'attachment; filename="{fname}"'},
            )
        return StreamingResponse(
            _stream_csv_hotspots(stmt, a.is_preset, a.geojson, getattr(a, "bbox_only", False)),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    if level == "daily":
        stmt = select(DailyAggregate).where(DailyAggregate.aoi_id == aoi)
        if eff_start:
            stmt = stmt.where(DailyAggregate.local_date >= eff_start)
        if eff_end:
            stmt = stmt.where(DailyAggregate.local_date <= eff_end)
        stmt = stmt.order_by(DailyAggregate.local_date, DailyAggregate.satellite)
        rows = db.execute(stmt).scalars().all()
        return _csv_response(
            fname,
            ["local_date", "satellite", "sensor", "n_detections", "n_strict", "n_day",
             "n_night", "frp_sum_mw", "frp_mean_mw", "pixel_area_sum_km2", "t4_mean_k"],
            (
                [r.local_date, r.satellite, r.sensor_family, r.n_detections, r.n_strict,
                 r.n_day, r.n_night, round(r.frp_sum_mw, 3), round(r.frp_mean_mw, 3),
                 round(r.pixel_area_sum_km2, 4), round(r.t4_mean_k, 2)]
                for r in rows
            ),
        )

    if level == "harmonized":
        stmt = select(HarmonizedDaily).where(HarmonizedDaily.aoi_id == aoi)
        if eff_start:
            stmt = stmt.where(HarmonizedDaily.local_date >= eff_start)
        if eff_end:
            stmt = stmt.where(HarmonizedDaily.local_date <= eff_end)
        stmt = stmt.order_by(HarmonizedDaily.local_date)
        rows = db.execute(stmt).scalars().all()
        return _csv_response(
            fname,
            ["local_date", "provenance", "n_modis", "n_viirs",
             "n_comparable_modis", "n_comparable_viirs",
             "n_harmonized", "n_harmonized_lo", "n_harmonized_hi",
             "n_static", "n_night", "static_share",
             "frp_modis_mw", "frp_viirs_mw", "frp_harmonized_mw",
             "frp_harmonized_lo_mw", "frp_harmonized_hi_mw",
             "era", "z_n", "pct_n", "flag_high", "flag_low"],
            (
                [r.local_date, r.provenance, r.n_modis, r.n_viirs,
                 r.n_comparable_modis, r.n_comparable_viirs,
                 round(r.n_harmonized, 3),
                 None if r.n_harmonized_lo is None else round(r.n_harmonized_lo, 3),
                 None if r.n_harmonized_hi is None else round(r.n_harmonized_hi, 3),
                 r.n_static, r.n_night,
                 None if r.static_share is None else round(r.static_share, 4),
                 round(r.frp_modis_mw, 3), round(r.frp_viirs_mw, 3),
                 round(r.frp_harmonized_mw, 3),
                 None if r.frp_harmonized_lo_mw is None else round(r.frp_harmonized_lo_mw, 3),
                 None if r.frp_harmonized_hi_mw is None else round(r.frp_harmonized_hi_mw, 3),
                 round(r.seam_weight, 2),
                 None if r.z_n is None else round(r.z_n, 3),
                 None if r.pct_n is None else round(r.pct_n, 2),
                 r.flag_high, r.flag_low]
                for r in rows
            ),
        )

    # episodes
    stmt = select(FireEpisode).where(FireEpisode.aoi_id == aoi)
    if eff_start:
        stmt = stmt.where(FireEpisode.end_date >= eff_start)
    if eff_end:
        stmt = stmt.where(FireEpisode.start_date <= eff_end)
    stmt = stmt.order_by(FireEpisode.start_date)
    rows = db.execute(stmt).scalars().all()
    if format == "geojson":
        return _geojson_response(
            fname,
            (
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [e.bounds_west, e.bounds_south],
                            [e.bounds_east, e.bounds_south],
                            [e.bounds_east, e.bounds_north],
                            [e.bounds_west, e.bounds_north],
                            [e.bounds_west, e.bounds_south],
                        ]],
                    },
                    "properties": {
                        "label": e.label, "start_date": e.start_date.isoformat(),
                        "end_date": e.end_date.isoformat(),
                        "duration_days": e.duration_days,
                        "n_detections": e.n_detections,
                        "peak_frp_mw": round(e.peak_frp_mw, 2),
                        "total_frp_mw": round(e.total_frp_mw, 2),
                        "intensity_index": round(e.intensity_index, 1),
                        "sensor_mix": json.loads(e.sensor_mix_json or "{}"),
                        "is_critical": e.is_critical,
                    },
                }
                for e in rows
            ),
        )
    return _csv_response(
        fname,
        ["label", "start_date", "end_date", "duration_days", "n_detections",
         "peak_frp_mw", "total_frp_mw", "intensity_index", "centroid_lat",
         "centroid_lon", "modis_detections", "viirs_detections", "is_critical"],
        (
            [e.label, e.start_date, e.end_date, e.duration_days, e.n_detections,
             round(e.peak_frp_mw, 2), round(e.total_frp_mw, 2),
             round(e.intensity_index, 1), round(e.centroid_lat, 5),
             round(e.centroid_lon, 5),
             json.loads(e.sensor_mix_json or "{}").get("MODIS", 0),
             json.loads(e.sensor_mix_json or "{}").get("VIIRS", 0),
             e.is_critical]
            for e in rows
        ),
    )
