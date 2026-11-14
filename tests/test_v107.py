"""Tests for v1.0.7: 6-AOI operational readiness, intra-VIIRS normalization,
calendar payload optimization, dual export, and dynamic AOI synthesis.
"""
from datetime import date, datetime
import os
import sys
import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend = os.path.join(_repo_root, "backend")
if os.path.exists(_backend) and _backend not in sys.path:
    sys.path.insert(0, _backend)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

os.environ.setdefault("EMBEREBEYE_DATABASE_URL", "sqlite:///./test_v107.db")

from starlette.testclient import TestClient

from app.db import SessionLocal, create_all
from app.main import app
import app.main as main_mod
from app.models import Aoi, Hotspot, IngestRun
from app.routers import aois as aois_mod
from app.routers import export as export_mod
from app.routers import meta as meta_mod
from app.seed.profiles import PRESET_PROFILES
from app.seed.pipeline import seed_unseeded_presets, ensure_preset_aois

create_all()

client = TestClient(app)


def test_seed_unseeded_presets_populates_all_presets():
    """Task 1: seed_unseeded_presets ensures all presets have data."""
    db = SessionLocal()
    try:
        ensure_preset_aois(db)
        rep = seed_unseeded_presets(db)
        assert rep["status"] == "OK"

        resp = client.get("/api/aois")
        assert resp.status_code == 200
        aois = resp.json()
        preset_ids = {p.aoi_id for p in PRESET_PROFILES}
        preset_map = {a["id"]: a for a in aois if a["is_preset"]}

        assert preset_ids.issubset(set(preset_map.keys()))
        for pid in preset_ids:
            assert preset_map[pid]["has_data"] is True, f"Preset {pid} should have data"
    finally:
        db.close()


def test_real_firms_data_protection():
    """Task 1: Any AOI holding real FIRMS rows (SP/NRT) is skipped and preserved."""
    db = SessionLocal()
    try:
        run = IngestRun(mode="BACKFILL", source="firms_csv", status="OK")
        db.add(run)
        db.commit()
        real_hotspot = Hotspot(
            aoi_id="nile-delta-egypt",
            sensor_family="MODIS", satellite="Terra", instrument="MODIS", resolution_m=1000,
            acq_dt_utc=datetime(2020, 5, 1, 10, 30), acq_date=date(2020, 5, 1), local_date=date(2020, 5, 1),
            lat=30.5, lon=31.0, confidence_class="HIGH", daynight="D",
            firms_product="SP", run_id=run.id,
        )
        db.add(real_hotspot)
        db.commit()

        # Re-running seed_unseeded_presets must skip real data
        rep = seed_unseeded_presets(db)
        assert rep["status"] == "OK"
        assert rep["results"]["nile-delta-egypt"]["status"] == "SKIPPED_REAL_DATA"

        # The real hotspot row must still exist
        check = db.query(Hotspot).filter(Hotspot.id == real_hotspot.id).first()
        assert check is not None
        assert check.firms_product == "SP"
    finally:
        db.close()


def test_calendar_start_and_end_year_filter():
    """Task 4: Calendar endpoint supports start_year and end_year filtering."""
    resp = client.get("/api/calendar?aoi=nile-delta-egypt&start_year=2020&end_year=2022")
    assert resp.status_code == 200
    data = resp.json()
    cells = data["cells"]
    assert len(cells) > 0
    years = {c["year"] for c in cells}
    assert years == {2020, 2021, 2022}


def test_calendar_compact_mode_payload():
    """Task 4: Calendar endpoint supports compact=true to minimize JSON payload."""
    resp_full = client.get("/api/calendar?aoi=nile-delta-egypt&start_year=2020&end_year=2020")
    assert resp_full.status_code == 200
    resp_compact = client.get("/api/calendar?aoi=nile-delta-egypt&start_year=2020&end_year=2020&compact=true")
    assert resp_compact.status_code == 200

    # Compact output is leaner
    assert len(resp_compact.content) <= len(resp_full.content)
    c0 = resp_compact.json()["cells"][0]
    assert "date" in c0
    assert "n_harmonized" in c0
    assert "frp_harmonized_mw" in c0
    assert "provenance" in c0


def test_dual_csv_export():
    """Task 5: Export endpoint returns both harmonized daily CSV and raw hotspots CSV."""
    # 1. Harmonized daily CSV
    resp_harm = client.get("/api/export?aoi=nile-delta-egypt&level=harmonized&format=csv&start=2020-01-01&end=2020-01-15")
    assert resp_harm.status_code == 200
    assert "text/csv" in resp_harm.headers.get("content-type", "")
    content_harm = resp_harm.text
    assert "local_date,provenance,n_modis,n_viirs" in content_harm

    # 2. Hotspots CSV
    resp_hotspots = client.get("/api/export?aoi=nile-delta-egypt&level=hotspots&format=csv&start=2020-01-01&end=2020-01-05")
    assert resp_hotspots.status_code == 200
    assert "text/csv" in resp_hotspots.headers.get("content-type", "")
    content_hotspots = resp_hotspots.text
    assert "local_date,acq_dt_utc,sensor,satellite" in content_hotspots


def test_intra_viirs_methodology_documented():
    """Task 2: /api/meta documents Intra-VIIRS constellation normalization."""
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    meta = resp.json()
    steps = meta.get("methodology", {}).get("steps", [])
    combined_steps = " ".join(steps)
    assert "Intra-VIIRS Constellation Normalization" in combined_steps
    assert "NOAA-20" in combined_steps
    assert "f_day" in combined_steps


def test_public_read_only_blocks_api_writes(monkeypatch):
    """Public mode must reject every mutating API method before route work."""
    cfg = meta_mod.get_settings()
    monkeypatch.setattr(cfg, "public_read_only", True)
    monkeypatch.setitem(meta_mod.__dict__, "get_settings", lambda: cfg)
    guard = next(
        middleware.kwargs["dispatch"]
        for middleware in app.user_middleware
        if getattr(middleware.kwargs.get("dispatch"), "__name__", "") == "public_read_only_guard"
    )
    monkeypatch.setitem(guard.__globals__, "get_settings", lambda: cfg)
    assert client.get("/api/meta").json()["public_read_only"] is True
    assert client.get("/api/health").status_code == 200

    requests = [
        ("post", "/api/aois", {"name": "blocked", "geojson": {}}),
        ("post", "/api/aois/example/rebuild", {}),
        ("delete", "/api/aois/example", None),
        ("post", "/api/live/fetch", {"aoi_id": "example"}),
    ]
    for method, path, payload in requests:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
        assert response.status_code == 403, (method, path, response.text)
        assert "read-only" in response.json()["detail"].lower()


def test_public_read_only_startup_skips_schema_ddl(monkeypatch):
    cfg = main_mod.get_settings()
    monkeypatch.setattr(cfg, "public_read_only", True)
    monkeypatch.setitem(main_mod.__dict__, "get_settings", lambda: cfg)
    monkeypatch.setattr(
        main_mod, "create_all",
        lambda: pytest.fail("public read-only startup must not run DDL"),
    )
    startup = next(
        handler for handler in main_mod.app.router.on_startup
        if handler.__name__ == "_startup"
    )
    startup()


def test_public_read_only_startup_skips_job_recovery(monkeypatch):
    cfg = aois_mod.get_settings()
    monkeypatch.setattr(cfg, "public_read_only", True)
    monkeypatch.setitem(aois_mod.__dict__, "get_settings", lambda: cfg)
    monkeypatch.setattr(
        aois_mod, "SessionLocal",
        lambda: pytest.fail("read-only startup must not open a write session"),
    )
    aois_mod._recover_interrupted_jobs()


def test_create_aoi_runs_aggregation_job(monkeypatch):
    """Regression: POST /api/aois used to fail its background job with
    ``NameError: name 'func' is not defined`` (missing SQLAlchemy import).

    The job is exercised end-to-end through the API with the heavy steps
    stubbed out, so the SQL coverage query itself is the thing under test.
    """
    import app.harmonize.pipeline as pipeline_mod
    import app.seed.synth as synth_mod
    from app.models import AggregationJob

    monkeypatch.setattr(synth_mod, "generate_aoi_records", lambda *a, **k: [])
    monkeypatch.setattr(pipeline_mod, "run_pipeline_for_aoi", lambda *a, **k: {"status": "OK"})

    resp = client.post(
        "/api/aois",
        json={
            "name": "Regression Box",
            "description": "tiny AOI used by the API regression test",
            "geojson": {
                "type": "Polygon",
                "coordinates": [[[24.0, 44.0], [24.4, 44.0], [24.4, 44.4], [24.0, 44.4], [24.0, 44.0]]],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    aoi_id = payload["id"]
    assert payload["has_data"] is False
    assert payload["job_id"]

    db = SessionLocal()
    try:
        job = db.query(AggregationJob).filter(AggregationJob.aoi_id == aoi_id).one()
        assert job.status == "DONE", job.message
        assert "NameError" not in (job.message or "")
        assert client.get(f"/api/jobs/{payload['job_id']}").json()["status"] == "DONE"
        assert client.get(f"/api/aois/{aoi_id}").json()["job_id"] == payload["job_id"]
    finally:
        db.close()

    assert client.delete(f"/api/aois/{aoi_id}").status_code == 204


def test_dynamic_aoi_profile_has_ignition_clusters():
    """Regression: an AOI drawn outside the archive synthesized a profile with
    no clusters, so the generator died with
    ``ValueError: a must be a positive integer unless no samples are taken``.
    """
    import json
    from dataclasses import replace

    from app.seed.pipeline import _profile_from_aoi
    from app.seed.synth import generate_aoi_records

    ring = [[100.0, 50.0], [100.5, 50.0], [100.5, 50.5], [100.0, 50.5], [100.0, 50.0]]
    aoi = Aoi(
        id="dynamic-profile-test", name="Dynamic", description="",
        geojson=json.dumps({"type": "Polygon", "coordinates": [ring]}),
        west=100.0, south=50.0, east=100.5, north=50.5, is_preset=False,
    )
    profile = _profile_from_aoi(aoi)
    assert profile.clusters, "dynamic AOIs need at least one ignition cluster"

    active = replace(profile, base_daily_events=1.8)
    records = generate_aoi_records(
        active, aoi.id, date(2020, 1, 1), date(2020, 3, 31), 1.0, "test-seed"
    )
    assert records, "an active background regime must produce detections"

    empty = replace(profile, clusters=(), base_daily_events=1.8)
    assert generate_aoi_records(
        empty, aoi.id, date(2020, 1, 1), date(2020, 3, 31), 1.0, "test-seed"
    ) == []


def test_health_aoi_count_matches_aoi_table():
    """Regression: /api/health counted SystemState rows (12) instead of AOIs."""
    db = SessionLocal()
    try:
        expected = db.query(Aoi).count()
    finally:
        db.close()
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["counts"]["aois"] == expected


def _ensure_seeded(aoi_id: str = "nile-delta-egypt") -> None:
    """Make the calendar-filter tests self-contained.

    The module normally starts with ``test_seed_unseeded_presets_*``, but a
    targeted run (``pytest -k calendar_filter``) must not depend on test order,
    so seed on demand. ``seed_unseeded_presets`` is idempotent and preserves
    any AOI that already has rows.
    """
    db = SessionLocal()
    try:
        ensure_preset_aois(db)
        if db.query(Hotspot).filter(Hotspot.aoi_id == aoi_id).count() == 0:
            report = seed_unseeded_presets(db)
            assert report["status"] == "OK"
    finally:
        db.close()


def _calendar(**params):
    query = {"aoi": "nile-delta-egypt", "compact": "true"}
    query.update(params)
    resp = client.get("/api/calendar", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _total(payload) -> float:
    return sum(c["n_harmonized"] for c in payload["cells"])


def test_calendar_filters_default_to_unfiltered_series():
    """Explicit default ribbon filters must reproduce the unfiltered payload.

    The calendar is fetched with the active ribbon filters on every request;
    an "all/all/all/no FRP/no static exclusion" request has to be byte-for-byte
    the same series as the legacy call, otherwise merely rendering the ribbon
    would perturb the headline numbers.
    """
    _ensure_seeded()
    base = _calendar(start_year=2020, end_year=2020)
    explicit = _calendar(
        start_year=2020,
        end_year=2020,
        sensor="all",
        confidence="all",
        daynight="all",
        hide_static="false",
    )
    assert base == explicit


def test_calendar_filter_sensor_family_is_exclusive():
    """MODIS and VIIRS selections must not leak the other instrument."""
    _ensure_seeded()
    modis = _calendar(start_year=2020, end_year=2020, sensor="MODIS")
    viirs = _calendar(start_year=2020, end_year=2020, sensor="VIIRS")

    assert _total(modis) > 0 and _total(viirs) > 0
    assert all(c["n_viirs"] == 0 for c in modis["cells"])
    assert all(c["n_modis"] == 0 for c in viirs["cells"])
    # Each slice stays internally consistent: the filtered total is exactly
    # the selected family's count for that day.
    assert all(c["n_modis"] == c["n_harmonized"] for c in modis["cells"])
    assert all(c["n_viirs"] == c["n_harmonized"] for c in viirs["cells"])


def test_calendar_filter_daynight_recomputes_night_only():
    """Night view is built from night detections, so n_night == n_harmonized."""
    _ensure_seeded()
    night = _calendar(start_year=2020, end_year=2020, daynight="N")
    assert _total(night) > 0
    assert all(c["n_night"] == c["n_harmonized"] for c in night["cells"])
    raw = _calendar(start_year=2020, end_year=2020, min_frp=0)
    assert _total(night) < _total(raw)


def test_calendar_filter_static_and_confidence_and_frp_reduce_totals():
    """Static exclusion / confidence / min-FRP shrink the series but keep the grid."""
    _ensure_seeded()
    raw = _calendar(start_year=2020, end_year=2020, min_frp=0)
    static_off = _calendar(start_year=2020, end_year=2020, min_frp=0, hide_static="true")
    high = _calendar(start_year=2020, end_year=2020, min_frp=0, confidence="HIGH")
    frp = _calendar(start_year=2020, end_year=2020, min_frp=50)

    assert len(static_off["cells"]) == len(raw["cells"]), "filters must keep the day grid"
    assert all(c["n_static"] == 0 for c in static_off["cells"])
    assert _total(static_off) < _total(raw)
    assert _total(high) < _total(raw)
    assert _total(frp) < _total(raw)


def test_calendar_filter_combined_matches_map_semantics():
    """The ribbon sends filters together; the combined slice stays coherent."""
    _ensure_seeded()
    combo = _calendar(
        start_year=2020,
        end_year=2020,
        sensor="VIIRS",
        daynight="N",
        confidence="HIGH",
        hide_static="true",
    )
    assert all(c["n_modis"] == 0 for c in combo["cells"])
    assert all(c["n_static"] == 0 for c in combo["cells"])
    assert all(c["n_night"] == c["n_harmonized"] for c in combo["cells"])


def test_calendar_filter_applies_before_viirs_seam():
    """Pre-seam years (MODIS terrain) also respond to the ribbon filters."""
    _ensure_seeded()
    base = _calendar(start_year=2011, end_year=2011, min_frp=0)
    filtered = _calendar(start_year=2011, end_year=2011, min_frp=0, hide_static="true")
    assert _total(base) > 0
    assert _total(filtered) < _total(base)


def test_geojson_streaming_export_fast_and_valid():
    """Fix 2: Hotspot GeoJSON streaming export completes quickly and produces valid JSON."""
    import json
    import time

    t0 = time.time()
    resp = client.get("/api/export?aoi=nile-delta-egypt&level=hotspots&format=geojson&start=2020-01-01&end=2020-01-10")
    duration = time.time() - t0
    assert resp.status_code == 200
    assert "application/geo+json" in resp.headers.get("content-type", "")
    assert duration < 2.0, f"Export took {duration:.2f}s, expected < 2.0s"

    data = json.loads(resp.text)
    assert data["type"] == "FeatureCollection"
    assert "features" in data
    assert isinstance(data["features"], list)
    if len(data["features"]) > 0:
        f0 = data["features"][0]
        assert f0["type"] == "Feature"
        assert f0["geometry"]["type"] == "Point"
        assert len(f0["geometry"]["coordinates"]) == 2
        assert "sensor" in f0["properties"]
        assert "frp_mw" in f0["properties"]


def test_hotspot_export_applies_frontend_filters():
    import csv
    import io

    _ensure_seeded()
    params = {
        "aoi": "nile-delta-egypt", "level": "hotspots", "format": "csv",
        "start": "2020-01-01", "end": "2020-03-31",
    }
    baseline = client.get("/api/export", params=params)
    assert baseline.status_code == 200
    rows_all = list(csv.DictReader(io.StringIO(baseline.text)))
    filtered_params = {
        **params, "sensor": "VIIRS", "confidence": "HIGH", "daynight": "D",
        "min_frp": 0.1, "hide_static": "true",
    }
    filtered = client.get("/api/export", params=filtered_params)
    assert filtered.status_code == 200
    rows_filtered = list(csv.DictReader(io.StringIO(filtered.text)))
    assert len(rows_filtered) <= len(rows_all)
    assert all(row["sensor"] == "VIIRS" for row in rows_filtered)
    assert all(row["confidence"] == "HIGH" for row in rows_filtered)
    assert all(row["daynight"] == "D" for row in rows_filtered)
    assert all(float(row["frp_mw"]) >= 0.1 for row in rows_filtered)
    assert all(row["static_source"].lower() in {"false", "0"} for row in rows_filtered)


@pytest.fixture()
def triangle_aoi():
    """AOI whose triangular interior excludes a point inside its bbox."""
    import json
    from app.models import AggregationJob, HarmonizedDaily

    _ensure_seeded()
    aoi_id = "challenge-integrity-triangle"
    source_tag = "challenge_integrity_test"
    with SessionLocal() as db:
        db.query(AggregationJob).filter(AggregationJob.aoi_id == aoi_id).delete()
        db.query(HarmonizedDaily).filter(HarmonizedDaily.aoi_id == aoi_id).delete()
        db.query(Hotspot).filter(Hotspot.source_version == source_tag).delete()
        db.query(Aoi).filter(Aoi.id == aoi_id).delete()
        db.query(IngestRun).filter(IngestRun.source == source_tag).delete()
        aoi = Aoi(
            id=aoi_id, name="Integrity triangle", description="", is_preset=False,
            geojson=json.dumps({"type": "Polygon", "coordinates": [[
                [0.0, 0.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0],
            ]]}),
            west=0.0, south=0.0, east=2.0, north=2.0,
        )
        db.add(aoi)
        run = IngestRun(mode="SEED", source=source_tag, status="OK", aoi_id=None)
        db.add(run)
        db.commit()
        day = date(2020, 1, 2)
        points = [
            (0.2, 0.2, "VIIRS", 2.0),
            (0.4, 0.2, "MODIS", 3.0),
            (1.8, 1.8, "VIIRS", 100.0),  # inside bbox, outside polygon
        ]
        for lon, lat, family, frp in points:
            db.add(Hotspot(
                aoi_id="nile-delta-egypt", sensor_family=family,
                satellite="SNPP" if family == "VIIRS" else "Terra",
                instrument=family, resolution_m=375 if family == "VIIRS" else 1000,
                acq_dt_utc=datetime(2020, 1, 2, 12), acq_date=day, local_date=day,
                lat=lat, lon=lon, brightness_t4_k=340.0,
                brightness_t11_k=300.0, frp_mw=frp, confidence_class="HIGH",
                confidence_raw=None, daynight="D", scan_km=0.5, track_km=0.5,
                pixel_area_km2=0.25, saturated=False, band21_fallback=False,
                static_source=False, recurring_source=False, source_version=source_tag,
                firms_product="", firms_type=None, run_id=run.id,
            ))
        db.add(HarmonizedDaily(
            aoi_id=aoi_id, local_date=day, n_modis=1, n_viirs=1,
            n_comparable_modis=1, n_comparable_viirs=1, n_harmonized=2.0,
            n_harmonized_lo=2.0, n_harmonized_hi=2.0,
            frp_modis_mw=3.0, frp_viirs_mw=2.0, frp_harmonized_mw=5.0,
            n_static=0, n_night=0, static_share=0.0,
            seam_weight=1.0, provenance="VIIRS_NATIVE", flag_high=False,
            flag_low=False,
        ))
        db.commit()
    yield aoi_id
    with SessionLocal() as db:
        db.query(AggregationJob).filter(AggregationJob.aoi_id == aoi_id).delete()
        db.query(HarmonizedDaily).filter(HarmonizedDaily.aoi_id == aoi_id).delete()
        db.query(Hotspot).filter(Hotspot.source_version == source_tag).delete()
        db.query(Aoi).filter(Aoi.id == aoi_id).delete()
        db.query(IngestRun).filter(IngestRun.source == source_tag).delete()
        db.commit()


def test_custom_polygon_is_consistent_across_map_grid_and_snapshot(triangle_aoi):
    import gzip
    import json
    import struct

    points = client.get(f"/api/hotspots?aoi={triangle_aoi}&limit=1")
    assert points.status_code == 200, points.text
    body = points.json()
    assert body["meta"]["total"] == 2
    assert body["meta"]["returned"] == 1
    assert body["meta"]["truncated"] is True
    assert body["meta"]["total_is_lower_bound"] is False
    assert all(x["geometry"]["coordinates"][0] + x["geometry"]["coordinates"][1] <= 2
               for x in body["features"])

    # A viewport inside the triangle bbox but outside the polygon must not
    # override the AOI's actual geometry.
    outside_view = client.get(
        f"/api/hotspots?aoi={triangle_aoi}&bbox=1.3,1.3,1.9,1.9"
    )
    assert outside_view.status_code == 200
    assert outside_view.json()["features"] == []

    grid = client.get(f"/api/hotspots/grid?aoi={triangle_aoi}&res=month&cell_deg=0.25")
    assert grid.status_code == 200
    grid_body = grid.json()
    assert grid_body["meta"]["total_points"] == 2
    assert sum(cell["n"] for cell in grid_body["cells"]) == 2

    snap = client.get(f"/api/hotspots/snapshot?aoi={triangle_aoi}")
    assert snap.status_code == 200
    payload = gzip.decompress(snap.content)
    hlen = struct.unpack("<I", payload[8:12])[0]
    header = json.loads(payload[12:12 + hlen])
    assert header["n"] == 2


def test_filtered_calendar_uses_custom_polygon_and_selected_period(triangle_aoi):
    response = client.get(
        "/api/calendar",
        params={
            "aoi": triangle_aoi, "start": "2020-01-02", "end": "2020-01-02",
            "sensor": "VIIRS",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [cell["date"] for cell in body["cells"]] == ["2020-01-02"]
    assert body["cells"][0]["n_viirs"] == 1
    assert body["cells"][0]["n_harmonized"] == 1
    assert body["weekly_cells"]
    assert sum(week["n_harmonized"] for week in body["weekly_cells"]) == 1


def test_hotspot_exports_report_truncation_in_headers_and_remain_valid(
    triangle_aoi, monkeypatch,
):
    import csv
    import io
    import json
    monkeypatch.setattr(export_mod, "HOTSPOT_EXPORT_LIMIT", 1)
    common = {
        "aoi": triangle_aoi, "level": "hotspots",
        "start": "2020-01-02", "end": "2020-01-02",
    }
    csv_response = client.get("/api/export", params={**common, "format": "csv"})
    assert csv_response.status_code == 200
    assert csv_response.headers["x-export-truncated"] == "true"
    assert csv_response.headers["x-export-total"] == "2"
    assert csv_response.headers["x-export-total-exact"] == "true"
    assert len(list(csv.DictReader(io.StringIO(csv_response.text)))) == 1

    geojson_response = client.get("/api/export", params={**common, "format": "geojson"})
    assert geojson_response.status_code == 200
    assert geojson_response.headers["x-export-truncated"] == "true"
    geojson = json.loads(geojson_response.text)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1


def test_filtered_calendar_rejects_reversed_period(triangle_aoi):
    response = client.get(
        "/api/calendar?aoi=%s&start=2020-01-03&end=2020-01-02" % triangle_aoi
    )
    assert response.status_code == 422


def test_rebuild_reuses_active_job_and_startup_fails_interrupted_jobs(
    triangle_aoi, monkeypatch,
):
    from app.models import AggregationJob
    with SessionLocal() as db:
        job = AggregationJob(
            id="challenge-job-active", aoi_id=triangle_aoi,
            status="RUNNING", progress=0.4, message="working",
        )
        db.add(job)
        db.commit()
    repeated = client.post(f"/api/aois/{triangle_aoi}/rebuild")
    assert repeated.status_code == 202
    assert repeated.json()["id"] == "challenge-job-active"
    with SessionLocal() as db:
        assert db.query(AggregationJob).filter(
            AggregationJob.aoi_id == triangle_aoi,
            AggregationJob.status.in_(("PENDING", "RUNNING")),
        ).count() == 1

    aois_mod._recover_interrupted_jobs()
    with SessionLocal() as db:
        recovered = db.get(AggregationJob, "challenge-job-active")
        assert recovered.status == "FAILED"
        assert "restart" in recovered.message

    # Once terminal, the same AOI can be queued again; do not run the heavy
    # background pipeline as part of this API concurrency regression.
    monkeypatch.setattr(aois_mod, "_run_aggregation_job", lambda *_: None)
    again = client.post(f"/api/aois/{triangle_aoi}/rebuild")
    assert again.status_code == 202
    assert again.json()["id"] != "challenge-job-active"
