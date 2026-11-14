"""Tests for v1.0.7: 6-AOI operational readiness, intra-VIIRS normalization,
calendar payload optimization, dual export, and dynamic AOI synthesis.
"""
from datetime import date, datetime
import os
import sys

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
from app.models import Aoi, Hotspot, IngestRun
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
    meta_route = next(route for route in app.routes if getattr(route, "path", None) == "/api/meta")
    meta_globals = meta_route.endpoint.__globals__
    cfg = meta_globals["get_settings"]()
    monkeypatch.setattr(cfg, "public_read_only", True)
    monkeypatch.setitem(meta_globals, "get_settings", lambda: cfg)
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
