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
from app.models import Aoi, HarmonizedDaily, Hotspot, IngestRun
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
    aoi_id = resp.json()["id"]

    db = SessionLocal()
    try:
        job = db.query(AggregationJob).filter(AggregationJob.aoi_id == aoi_id).one()
        assert job.status == "DONE", job.message
        assert "NameError" not in (job.message or "")
    finally:
        db.close()

    assert client.delete(f"/api/aois/{aoi_id}").status_code == 204


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

