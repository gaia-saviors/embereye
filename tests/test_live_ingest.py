"""Regression tests for live FIRMS ingestion and mode separation."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from types import SimpleNamespace

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend = os.path.join(_repo_root, "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


def _settings(map_key: str = "test-map-key"):
    return SimpleNamespace(
        firms_map_key=map_key,
        dedup_max_gap_minutes=90,
        dedup_max_distance_km_modis=0.8,
        dedup_max_distance_km_viirs=0.2,
    )


def _csv_for(source: str, satellite: str | None = None) -> str:
    if source == "MODIS_NRT":
        sat = satellite or "T"
        return (
            "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,"
            "instrument,confidence,version,bright_t31,frp,daynight\n"
            f"30.0,31.0,340,1,1,2026-11-13,1030,{sat},MODIS,65,6.1,300,4.2,D\n"
        )
    sat = satellite or ("N20" if source == "VIIRS_NOAA20_NRT" else "N21")
    return (
        "latitude,longitude,bright_ti4,bright_ti5,scan,track,acq_date,acq_time,"
        "satellite,instrument,confidence,version,frp,daynight\n"
        f"30.1,31.1,340,300,0.5,0.5,2026-11-13,1040,{sat},VIIRS,h,2.0,2.5,D\n"
    )


@pytest.fixture

def db(tmp_path):
    os.environ["EMBEREBEYE_DATABASE_URL"] = f"sqlite:///{tmp_path}/live.db"
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]
    from app.db import SessionLocal, create_all
    from app.models import Aoi

    create_all()
    session = SessionLocal()
    session.add(Aoi(
        id="test", name="Test AOI", geojson="{}", west=30, south=29,
        east=32, north=31, is_preset=False,
    ))
    session.commit()
    yield session
    session.close()


def _add_hotspot(db, product: str, satellite: str, acq_date: date, run_id: int):
    from app.models import Hotspot

    db.add(Hotspot(
        aoi_id="test", sensor_family="VIIRS", satellite=satellite,
        instrument="VIIRS", resolution_m=375,
        acq_dt_utc=datetime(acq_date.year, acq_date.month, acq_date.day, 10),
        acq_date=acq_date, local_date=acq_date, lat=30.0, lon=31.0,
        brightness_t4_k=340, brightness_t11_k=300, frp_mw=2,
        confidence_class="HIGH", confidence_raw=None, daynight="D",
        scan_km=0.5, track_km=0.5, pixel_area_km2=0.25,
        saturated=False, band21_fallback=False, static_source=False,
        firms_product=product, run_id=run_id,
    ))
    db.commit()


def _add_run(db, mode: str, source: str) -> int:
    from app.models import IngestRun

    run = IngestRun(mode=mode, source=source, aoi_id="test", status="OK")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run.id


def test_repeat_live_fetch_is_idempotent_and_keeps_provenance(db, monkeypatch):
    from app.ingest.firms_client import FetchResult
    from app.models import Hotspot, IngestRun
    from app.seed import live_ingest

    monkeypatch.setattr(live_ingest, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        live_ingest,
        "fetch_area_csv",
        lambda source, *_args: FetchResult(source=source, status="OK", text=_csv_for(source)),
    )
    monkeypatch.setattr(live_ingest, "run_pipeline_for_aoi", lambda *_args: {"rebuilt": True})

    seed_run = _add_run(db, "SEED", "synthetic")
    archive_run = _add_run(db, "LIVE", "firms_archive")
    _add_hotspot(db, "", "SNPP", date(2026, 11, 13), seed_run)
    _add_hotspot(db, "SP", "N20", date(2026, 11, 13), archive_run)

    aoi = db.get(__import__("app.models", fromlist=["Aoi"]).Aoi, "test")
    first = live_ingest.ingest_firms_for_aoi(db, aoi, day_range=2, day=date(2026, 11, 12))
    first_run = db.query(IngestRun).filter(IngestRun.source == "firms_api").order_by(
        IngestRun.id.desc()
    ).first()
    assert json.loads(first_run.meta_json)["synthetic_rows_removed"] == 1
    second = live_ingest.ingest_firms_for_aoi(db, aoi, day_range=2, day=date(2026, 11, 12))

    assert first["status"] == second["status"] == "OK"
    assert first["inserted"] == second["inserted"] == 3
    rows = db.query(Hotspot).filter(Hotspot.aoi_id == "test").all()
    nrt = [row for row in rows if row.firms_product == "NRT"]
    assert sorted((row.sensor_family, row.satellite) for row in nrt) == [
        ("MODIS", "Terra"), ("VIIRS", "N20"), ("VIIRS", "N21")
    ]
    assert sum(row.firms_product == "SP" for row in rows) == 1
    assert sum(row.firms_product == "" for row in rows) == 0
    latest_run = db.query(IngestRun).filter(IngestRun.source == "firms_api").order_by(
        IngestRun.id.desc()
    ).first()
    meta = json.loads(latest_run.meta_json)
    assert meta["provenance"]["product"] == "NRT"
    assert meta["provenance"]["near_real_time"] is True
    assert meta["provenance"]["science_quality"] == "provisional_unreviewed"
    assert "not MODIS-to-VIIRS-equivalent" in meta["provenance"]["uncertainty"]
    assert "NOAA-20/NOAA-21" in meta["provenance"]["platform_policy"]
    assert meta["synthetic_rows_removed"] == 0


def test_missing_map_key_fails_without_network_or_seed_fallback(db, monkeypatch):
    from app.seed import live_ingest
    from app.models import IngestRun

    monkeypatch.setattr(live_ingest, "get_settings", lambda: _settings(map_key=""))
    monkeypatch.setattr(
        live_ingest, "fetch_area_csv",
        lambda *_args: pytest.fail("must not contact FIRMS without MAP_KEY"),
    )
    result = live_ingest.ingest_firms_for_aoi(db, db.get(
        __import__("app.models", fromlist=["Aoi"]).Aoi, "test"
    ))
    run = db.query(IngestRun).filter(IngestRun.source == "firms_api").one()
    assert result["status"] == run.status == "FAILED"
    assert "FIRMS_MAP_KEY" in result["message"]
    assert "no seed fallback" in result["message"]


def test_network_failure_preserves_existing_firms_rows(db, monkeypatch):
    from app.ingest.firms_client import FetchResult
    from app.models import Hotspot
    from app.seed import live_ingest

    run_id = _add_run(db, "LIVE", "firms_api")
    _add_hotspot(db, "NRT", "N20", date(2026, 11, 13), run_id)
    monkeypatch.setattr(live_ingest, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        live_ingest,
        "fetch_area_csv",
        lambda source, *_args: FetchResult(
            source=source, status="UNREACHABLE", error="connection reset"
        ),
    )

    aoi = db.get(__import__("app.models", fromlist=["Aoi"]).Aoi, "test")
    result = live_ingest.ingest_firms_for_aoi(db, aoi, day_range=2, day=date(2026, 11, 14))
    assert result["status"] == "FAILED"
    assert "connection reset" in result["message"]
    assert db.query(Hotspot).filter(Hotspot.firms_product == "NRT").count() == 1


def test_source_platform_mismatch_does_not_admit_snpp_as_current_nrt(db, monkeypatch):
    from app.ingest.firms_client import FetchResult
    from app.models import Hotspot
    from app.seed import live_ingest

    monkeypatch.setattr(live_ingest, "get_settings", lambda: _settings())

    def fetch(source, *_args):
        sat = "SNPP" if source == "VIIRS_NOAA20_NRT" else None
        return FetchResult(source=source, status="OK", text=_csv_for(source, sat))

    monkeypatch.setattr(live_ingest, "fetch_area_csv", fetch)
    monkeypatch.setattr(live_ingest, "run_pipeline_for_aoi", lambda *_args: {})
    aoi = db.get(__import__("app.models", fromlist=["Aoi"]).Aoi, "test")
    result = live_ingest.ingest_firms_for_aoi(db, aoi, day_range=2, day=date(2026, 11, 12))
    assert result["status"] == "PARTIAL"
    assert any(entry["status"] == "PLATFORM_MISMATCH" for entry in result["per_source"])
    assert db.query(Hotspot).filter(Hotspot.satellite == "SNPP").count() == 0
    assert db.query(Hotspot).filter(Hotspot.satellite == "N21").count() == 1


def test_live_mode_rejects_missing_key_and_unreachable_firms(monkeypatch):
    from app.seed import pipeline

    monkeypatch.setattr(
        pipeline, "get_settings",
        lambda: SimpleNamespace(data_mode="LIVE", firms_map_key=""),
    )
    with pytest.raises(RuntimeError, match="requires FIRMS_MAP_KEY"):
        pipeline.decide_mode(None)

    monkeypatch.setattr(
        pipeline, "get_settings",
        lambda: SimpleNamespace(data_mode="LIVE", firms_map_key="configured"),
    )
    monkeypatch.setattr(
        pipeline, "check_key",
        lambda: {"reachable": False, "detail": "ConnectTimeout"},
    )
    with pytest.raises(RuntimeError, match="reachable FIRMS service"):
        pipeline.decide_mode(None)


def test_live_seed_entrypoint_never_calls_synthetic_generator(monkeypatch):
    from app import db as app_db
    from app.seed import pipeline

    settings = SimpleNamespace(
        data_mode="LIVE", firms_map_key="configured", log_level="CRITICAL",
        seed_version="test", seed_scale=1.0,
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "ensure_preset_aois", lambda _db: [SimpleNamespace(id="test")])
    monkeypatch.setattr(pipeline, "decide_mode", lambda _db: ("LIVE", "live enabled"))
    monkeypatch.setattr(pipeline, "count_real_hotspots", lambda _db: {})
    monkeypatch.setattr(pipeline, "_live_ingest_all", lambda *_args: {
        "test": {"status": "OK", "rows": 3, "message": "ok", "pipeline": {}}
    })
    monkeypatch.setattr(
        pipeline, "_seed_synthetic",
        lambda *_args: pytest.fail("LIVE must not generate or overlay synthetic data"),
    )
    fake_db = SimpleNamespace(
        close=lambda: None, commit=lambda: None, get=lambda *_args: None,
        add=lambda *_args: pytest.fail("LIVE must not update seed completion state"),
    )
    monkeypatch.setattr(app_db, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(app_db, "create_all", lambda: None)

    report = pipeline.run_seed(force=True)
    assert report["status"] == "OK"
    assert report["effective_mode"] == "LIVE"
    assert "synthetic_underlay" not in report
    assert "synthetic" not in report
