"""v1.0.6 tests (handoff round 2026-11-02).

Covers:
  P1  /hotspots/snapshot  binary round-trip, ETag/304, grid redirect cap
  P1  /hotspots/grid      server-side aggregation (week/month buckets)
  P1  /hotspots?bbox=     viewport-bounded raw fetch with honest meta
  P2b /meta.data_source   badge derived from data, never LIVE for an archive
  P2d /analytics/headline month-to-date vs SAME day-window median + suppression
  P2a moy.py              weekly/monthly LOYO at aggregation level
"""
from __future__ import annotations

import gzip
import json
import os
import struct
import sys
from datetime import date, timedelta

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend = os.path.join(_repo_root, "backend")
if os.path.exists(_backend) and _backend not in sys.path:
    sys.path.insert(0, _backend)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

os.environ["EMBEREBEYE_DATABASE_URL"] = "sqlite:///./test_v106.db"
for m in list(sys.modules):
    if m.startswith("app"):
        del sys.modules[m]

# Remove the previous run's database BEFORE the engine opens it: on Windows an
# open SQLite file cannot be unlinked, which made reruns inherit stale rows
# (and test_v107 shares this database, so its SP fixture row poisoned the
# SYNTHETIC_SEED badge assertion on the next run).
if os.path.exists("./test_v106.db"):
    os.remove("./test_v106.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import create_all  # noqa: E402
from app.main import app  # noqa: E402
from app.seed.pipeline import run_seed  # noqa: E402

create_all()
os.environ["SEED_SCALE"] = "0.02"
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
run_seed(force=True)

# NOTE: import every app symbol ONCE at module scope. Function-level
# re-imports would silently re-resolve to a re-initialised app.db when
# another test file purges sys.modules (different DB binding).
from app.db import SessionLocal  # noqa: E402
from app.models import Aoi, HarmonizedDaily, Hotspot  # noqa: E402
from app.routers import hotspots as _hotspots_mod  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# P1: columnar snapshot
# ---------------------------------------------------------------------------
class TestSnapshot:
    def test_snapshot_binary_roundtrip(self, client):
        r = client.get("/api/hotspots/snapshot?aoi=punjab-india")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/octet-stream")
        assert "etag" in r.headers
        raw = gzip.decompress(r.content)
        assert raw[:8] == b"EMBSNAP1"
        hlen = struct.unpack("<I", raw[8:12])[0]
        header = json.loads(raw[12:12 + hlen])
        assert header["mode"] == "snapshot"
        assert header["stride"] == 21
        n = header["n"]
        assert len(raw) == 12 + hlen + n * 21
        # rows sorted by day: scan the day column
        base = 12 + hlen
        days = [struct.unpack_from("<H", raw, base + i * 21)[0] for i in range(0, min(n, 500))]
        assert days == sorted(days)
        # decode one record fully
        d, lat_i, lon_i, acq_day, acq_min, sat, frp, t4, conf, flags = \
            struct.unpack_from("<HiiHHBHhBB", raw, base)
        assert 0 <= sat <= 5
        assert -90e5 <= lat_i <= 90e5 and -180e5 <= lon_i <= 180e5
        assert header["day_min"] is not None and header["day_max"] is not None

    def test_snapshot_etag_304(self, client):
        r1 = client.get("/api/hotspots/snapshot?aoi=punjab-india")
        etag = r1.headers["etag"]
        r2 = client.get("/api/hotspots/snapshot?aoi=punjab-india",
                        headers={"If-None-Match": etag})
        assert r2.status_code == 304
        assert r2.content == b""

    def test_snapshot_redirect_when_over_cap(self, client, monkeypatch):
        # patch the SAME module instance the app under test uses
        monkeypatch.setattr(_hotspots_mod, "SNAPSHOT_MAX_ROWS", 10)
        r = client.get("/api/hotspots/snapshot?aoi=punjab-india")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "grid"
        assert body["n_rows"] > 10
        assert "grid" in body["detail"]

    def test_snapshot_respects_attribute_filters(self, client):
        ra = client.get("/api/hotspots/snapshot?aoi=punjab-india")
        rb = client.get("/api/hotspots/snapshot?aoi=punjab-india&sensor=MODIS")
        assert rb.headers["etag"] != ra.headers["etag"]  # different cache key
        n_all = int(ra.headers["x-snapshot-rows"])
        n_mod = int(rb.headers["x-snapshot-rows"])
        assert 0 <= n_mod <= n_all


# ---------------------------------------------------------------------------
# P1: grid aggregation + bbox raw fetch
# ---------------------------------------------------------------------------
class TestGridAndBbox:
    def test_grid_month_buckets_sum_to_total(self, client):
        r = client.get("/api/hotspots/grid?aoi=punjab-india&res=month&cell_deg=0.5")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "grid" and body["res"] == "month"
        assert sum(c["n"] for c in body["cells"]) == body["meta"]["total_points"]
        assert body["meta"]["total_points"] > 0
        for c in body["cells"]:
            assert len(c["bucket"]) == 7  # YYYY-MM
            assert c["e"] > c["w"] and c["n_"] > c["s"]

    def test_grid_week_buckets_are_mondays(self, client):
        from datetime import datetime

        r = client.get("/api/hotspots/grid?aoi=punjab-india&res=week&cell_deg=0.5")
        body = r.json()
        assert body["res"] == "week"
        for c in body["cells"][:20]:
            d = datetime.strptime(c["bucket"], "%Y-%m-%d").date()
            assert d.weekday() == 0  # ISO Monday anchor

    def test_grid_day_window_filter(self, client):
        ra = client.get("/api/hotspots/grid?aoi=punjab-india&res=month")
        rb = client.get(
            "/api/hotspots/grid?aoi=punjab-india&res=month&start=2020-01-01&end=2020-12-31")
        assert rb.json()["meta"]["total_points"] <= ra.json()["meta"]["total_points"]

    def test_grid_hide_static_filter(self, client):
        all_rows = client.get("/api/hotspots/grid?aoi=punjab-india&res=month").json()
        filtered = client.get(
            "/api/hotspots/grid?aoi=punjab-india&res=month&hide_static=true").json()
        assert filtered["meta"]["total_points"] <= all_rows["meta"]["total_points"]
        assert all(cell["n_static"] == 0 for cell in filtered["cells"])

    def test_hotspots_bbox_overrides_spatial_filter(self, client):
        # a tiny bbox far from the AOI must return zero rows, honestly
        r = client.get(
            "/api/hotspots?aoi=punjab-india&bbox=0.0,0.0,0.5,0.5&limit=100")
        assert r.status_code == 200
        assert r.json()["meta"]["total"] == 0
        # a bbox covering the AOI returns rows and is capped
        r2 = client.get(
            "/api/hotspots?aoi=punjab-india&bbox=70,25,80,35&limit=5")
        body = r2.json()
        assert body["meta"]["returned"] <= 5
        assert body["meta"]["total"] >= body["meta"]["returned"]

    def test_hotspots_bbox_hide_static_filters_rows_and_total(self, client):
        params = "aoi=punjab-india&bbox=70,25,80,35&limit=20000"
        all_rows = client.get(f"/api/hotspots?{params}").json()
        filtered = client.get(f"/api/hotspots?{params}&hide_static=true").json()
        assert filtered["meta"]["total"] <= all_rows["meta"]["total"]
        assert all(not feature["properties"]["static_source"] for feature in filtered["features"])
        assert filtered["meta"]["returned"] == len(filtered["features"])
        assert filtered["meta"]["total"] >= filtered["meta"]["returned"]

    def test_hotspots_limit_capped_at_20000(self, client):
        r = client.get("/api/hotspots?aoi=punjab-india&limit=200000")
        assert r.status_code == 422  # le=20000 rejects the old raw dump


# ---------------------------------------------------------------------------
# P2b: data-source badge derived from the data
# ---------------------------------------------------------------------------
class TestDataSourceBadge:
    def test_seed_archive_badge(self, client):
        m = client.get("/api/meta").json()
        ds = m["data_source"]
        assert ds["kind"] == "SYNTHETIC_SEED"
        assert ds["auto_refresh"] is False
        assert "LIVE" not in ds["label"]
        assert "synthetic" in ds["detail"].lower()
        assert ds["n_hotspots"] > 0

    def test_firms_archive_badge(self, client):
        # flip the population: real SP rows only -> FIRMS_ARCHIVE, never LIVE
        from sqlalchemy import update

        with SessionLocal() as db:
            db.execute(update(Hotspot).values(firms_product="SP"))
            db.commit()
        try:
            m = client.get("/api/meta").json()
            ds = m["data_source"]
            assert ds["kind"] == "FIRMS_ARCHIVE"
            assert ds["auto_refresh"] is False
            assert "LIVE" not in ds["label"]
            # Phase 3A (defect 1): one plain wording - real data, record
            # range, and an explicit "not live"; the word STATIC is retired.
            assert "NASA FIRMS archive (real data)" in ds["label"]
            assert "not live" in ds["label"]
            assert ds["last_acquisition"] is not None
        finally:
            from sqlalchemy import update as u2

            with SessionLocal() as db:
                db.execute(u2(Hotspot).values(firms_product=""))
                db.commit()


# ---------------------------------------------------------------------------
# P2d: month-to-date headline vs SAME day-of-month window
# ---------------------------------------------------------------------------
class TestHeadlineMonthToDate:
    @pytest.fixture()
    def aoi_with_partial_month(self):
        """Fresh AOI whose record ends on Nov 6, with Nov 1-6 history for
        three prior years -> same-window median is computable."""
        aid = "v106-mtd"
        with SessionLocal() as db:
            db.merge(Aoi(
                id=aid, name="mtd", description="",
                geojson='{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}',
                west=0, south=0, east=1, north=1, is_preset=False,
                created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            ))
            db.query(HarmonizedDaily).filter(HarmonizedDaily.aoi_id == aid).delete()
            days = []
            for y in (2023, 2024, 2025):
                for dd in range(1, 7):
                    days.append(HarmonizedDaily(
                        aoi_id=aid, local_date=date(y, 11, dd),
                        n_modis=0, n_viirs=10 * dd, n_harmonized=10 * dd,
                        n_static=0, n_night=0, static_share=None,
                        frp_modis_mw=0, frp_viirs_mw=0, frp_harmonized_mw=0,
                        seam_weight=1.0, provenance="VIIRS_NATIVE",
                    ))
            # current month: Nov 1..6 of 2026 (6 days -> above min days)
            for dd in range(1, 7):
                days.append(HarmonizedDaily(
                    aoi_id=aid, local_date=date(2026, 11, dd),
                    n_modis=0, n_viirs=100, n_harmonized=100,
                    n_static=0, n_night=0, static_share=None,
                    frp_modis_mw=0, frp_viirs_mw=0, frp_harmonized_mw=0,
                    seam_weight=1.0, provenance="VIIRS_NATIVE",
                ))
            db.add_all(days)
            db.commit()
        yield aid
        with SessionLocal() as db:
            db.query(HarmonizedDaily).filter(HarmonizedDaily.aoi_id == aid).delete()
            db.query(Aoi).filter(Aoi.id == aid).delete()
            db.commit()

    def test_same_window_median(self, client, aoi_with_partial_month):
        r = client.get(f"/api/analytics/headline?aoi={aoi_with_partial_month}")
        body = r.json()
        # history: days 1-6 totals = 10+20+...+60 = 210 every year
        assert body["days_in_month"] == 6
        assert body["historical_median"] == 210.0
        assert body["historical_years"] == [2023, 2024, 2025]
        assert body["current_total"] == 600.0
        assert body["current_partial"] is True
        assert "above" in body["verdict"]
        assert body["min_days_for_verdict"] == 5

    def test_verdict_suppressed_early_month(self, client, aoi_with_partial_month):
        from sqlalchemy import delete

        # drop Nov 3-6 of 2026 -> only 2 days into the month
        with SessionLocal() as db:
            db.execute(delete(HarmonizedDaily).where(
                HarmonizedDaily.aoi_id == aoi_with_partial_month,
                HarmonizedDaily.local_date >= date(2026, 11, 3)))
            db.commit()
        r = client.get(f"/api/analytics/headline?aoi={aoi_with_partial_month}")
        body = r.json()
        assert body["days_in_month"] == 2
        assert body["historical_median"] is None
        assert body["percentile_rank"] is None
        assert body["verdict"].startswith("no verdict")
        assert "2 day" in body["verdict"]


# ---------------------------------------------------------------------------
# P2a: aggregation-level LOYO (monthly + weekly) in moy.py
# ---------------------------------------------------------------------------
class TestMoyAggregationLoyo:
    def _synthetic_pairs(self):
        rng = _DetRng(42)
        pooled, terra, aqua, daily = {}, {}, {}, {}
        for y in range(2012, 2023):
            pooled[y], terra[y], aqua[y] = {}, {}, {}
            ords, ms, vs = [], [], []
            for m in range(1, 13):
                mm = 500 + 30 * m + rng.next(0, 40)
                factor = 5.0 + (m % 4)
                vv = mm * factor * (1 + (rng.next(-8, 8) / 100))
                pooled[y][m] = (float(mm), float(vv))
                terra[y][m] = (mm * 0.6, vv * 0.6)
                aqua[y][m] = (mm * 0.4, vv * 0.4)
                for dd in range(1, 28, 3):
                    o = date(y, m, dd).toordinal()
                    ords.append(o)
                    ms.append(mm / 9)
                    vs.append(vv / 9)
            daily[y] = (ords, ms, vs)
        return pooled, terra, aqua, daily

    def test_weekly_loyo_runs_and_errors_are_weekly(self):
        from app.harmonize.moy import fit_moy_transfer

        pooled, terra, aqua, daily = self._synthetic_pairs()
        t = fit_moy_transfer(pooled, terra, aqua, daily_by_year=daily)
        assert t is not None
        assert t.loyo_weekly["n_errors"] > 0
        assert -1.0 < t.loyo_weekly["pi_low"] < t.loyo_weekly["pi_high"]
        assert t.loyo_monthly["n_errors"] > 0
        params = t.as_params()
        assert params["n_weekly_errors"] == t.loyo_weekly["n_errors"]
        assert params["n_monthly_errors"] == t.loyo_monthly["n_errors"]
        assert "aggregation-matched" in params["interval_policy"]

    def test_weekly_loyo_without_daily_counts_is_labeled(self):
        from app.harmonize.moy import fit_moy_transfer

        pooled, terra, aqua, daily = self._synthetic_pairs()
        t = fit_moy_transfer(pooled, terra, aqua)
        assert t is not None
        assert t.loyo_weekly["n_errors"] == 0
        assert "not supplied" in t.loyo_weekly.get("note", "")


class _DetRng:
    """Tiny deterministic RNG (no numpy dependence on ordering)."""

    def __init__(self, seed: int) -> None:
        self.s = seed

    def next(self, lo: int, hi: int) -> int:
        self.s = (self.s * 1103515245 + 12345) % (2 ** 31)
        return lo + self.s % (hi - lo + 1)


# ---------------------------------------------------------------------------
# Phase 3A: calendar weekly transferred era, complete-month headline,
# VIIRS-era static share, AOI has_data (defects 3/6/7/10)
# ---------------------------------------------------------------------------
class TestPhase3A:
    def test_calendar_has_weekly_transferred_cells_and_seam(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        c = client.get(f"/api/calendar?aoi={aoi}").json()
        assert "viirs_start_date" in c
        assert "transferred_weeks" in c
        if c["transferred_weeks"]:
            w = c["transferred_weeks"][0]
            for k in ("year", "iso_week", "start_date", "end_date",
                      "n_harmonized", "flag_high", "flag_low", "n_days_with_data"):
                assert k in w
            # weekly anomaly flags carry a LOYO percentile when flagged
            flagged = [w for w in c["transferred_weeks"] if w["flag_high"]]
            assert all(w["pct_n"] is not None and w["pct_n"] >= 95.0 for w in flagged)
            # the weekly baseline never includes VIIRS-native years
            assert all(w["year"] < 2012 or w["start_date"] < (c["viirs_start_date"] or "9999")
                       for w in c["transferred_weeks"])

    def test_headline_complete_month_is_last_full_month(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        hl = client.get(f"/api/analytics/headline?aoi={aoi}").json()
        assert "complete_month" in hl and "complete_total" in hl
        if hl["current_partial"]:
            # defect 6: the primary figure is the previous complete month
            assert hl["complete_month"] is not None
            assert hl["complete_month"] != hl["month"]
            assert hl["complete_total"] is not None
        else:
            assert hl["complete_month"] == hl["month"]

    def test_static_share_is_viirs_era_only(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        s = client.get(f"/api/analytics/summary?aoi={aoi}").json()
        if s.get("static_share") is None:
            pytest.skip("no static layer in this record")
        assert s["static_share_period_start"] == "2012-01-20"
        assert 0.0 <= s["static_share"] <= 1.0
        assert s["static_share_numerator"] <= s["static_share_denominator"]
        # the definition states the era restriction
        assert "VIIRS era" in (s["static_share_definition"] or "")

    def test_aoi_has_data_flag(self, client):
        aois = client.get("/api/aois").json()
        assert aois, "seed must create at least one AOI"
        assert all("has_data" in a for a in aois)
        assert any(a["has_data"] for a in aois)

    def test_summary_reports_per_era_season_medians(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        s = client.get(f"/api/analytics/summary?aoi={aoi}").json()
        assert "season_median_transferred_era" in s
        assert "season_median_viirs_era" in s
        assert "season_length_definition" in s


# ---------------------------------------------------------------------------
# STEP B: weekly cells for the whole record (one scale/unit), the headline
# finding, NRT provisional labelling, 80%-of-activity season definition
# ---------------------------------------------------------------------------
class TestStepB:
    def test_calendar_weekly_cells_cover_both_eras(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        c = client.get(f"/api/calendar?aoi={aoi}").json()
        assert "weekly_cells" in c and "finding" in c
        wk = c["weekly_cells"]
        if not wk:
            pytest.skip("no weekly rows in this seed")
        provs = {w["provenance"] for w in wk}
        assert provs <= {"MODIS_TRANSFERRED", "VIIRS_NATIVE"}
        assert len(provs) == 2, "seed record spans both eras"
        # every weekly row is anchored on an ISO Monday and spans 7 days
        from datetime import date as _date
        for w in wk[:50]:
            assert _date.fromisoformat(w["start_date"]).weekday() == 0
            assert (_date.fromisoformat(w["end_date"])
                    - _date.fromisoformat(w["start_date"])).days == 6
        # the transferred subset matches the compat view exactly
        tw = {(w["year"], w["iso_week"]): w for w in c["transferred_weeks"]}
        for w in wk:
            if w["provenance"] == "MODIS_TRANSFERRED":
                assert (w["year"], w["iso_week"]) in tw
                assert tw[(w["year"], w["iso_week"])]["n_harmonized"] == w["n_harmonized"]
        # weekly flags stay in-era: a transferred week's LOYO sample never
        # includes native years (guard preserved by construction)
        hi_native = [w for w in wk if w["provenance"] == "VIIRS_NATIVE" and w["flag_high"]]
        assert all(w["pct_n"] is not None and w["pct_n"] >= 95.0 for w in hi_native)

    def test_calendar_finding_numbers_are_consistent(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        c = client.get(f"/api/calendar?aoi={aoi}").json()
        f = c.get("finding")
        if not f:
            pytest.skip("no finding for this seed")
        wk = c["weekly_cells"]
        total = sum(w["n_harmonized"] for w in wk)
        assert abs(total - f["annual_total"]) < 1.0
        assert 0.0 <= f["sep_oct_share"] <= 100.0
        # the two era baselines bracket the overall share
        lo = min(x for x in (f["sep_oct_share_transferred"],
                             f["sep_oct_share_native"]) if x is not None)
        hi = max(x for x in (f["sep_oct_share_transferred"],
                             f["sep_oct_share_native"]) if x is not None)
        assert lo - 0.1 <= f["sep_oct_share"] <= hi + 0.1
        assert 1 <= f["w80_weeks_span"] <= 53
        assert f["w80_weeks_span"] == abs(f["w80_end_week"] - f["w80_start_week"]) + 1 or \
            f["w80_end_week"] < f["w80_start_week"]

    def test_headline_provisional_fields_present(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        hl = client.get(f"/api/analytics/headline?aoi={aoi}").json()
        assert "provisional" in hl and "nrt_share_month" in hl
        assert "nrt_share_complete" in hl and "provisional_note" in hl
        for k in ("nrt_share_month", "nrt_share_complete"):
            assert hl[k] is None or 0.0 <= hl[k] <= 100.0
        if hl["provisional"]:
            assert "NRT" in hl["provisional_note"]

    def test_season_definition_is_80pct(self, client):
        aois = client.get("/api/aois").json()
        aoi = aois[0]["id"]
        s = client.get(f"/api/analytics/summary?aoi={aoi}").json()
        assert "80%" in (s["season_length_definition"] or "")
        # a recomputed season window must actually hold >= 80% of its year
        with SessionLocal() as db:
            from app.models import SeasonMetrics
            rows = db.query(SeasonMetrics).filter(SeasonMetrics.aoi_id == aoi).order_by(SeasonMetrics.year).all()
            hd = db.query(HarmonizedDaily).filter(HarmonizedDaily.aoi_id == aoi).order_by(HarmonizedDaily.local_date).all()
        if not rows:
            pytest.skip("no season metrics stored in this seed")
        by_year = {}
        for r in hd:
            by_year.setdefault(r.local_date.year, 0.0)
            by_year[r.local_date.year] += float(r.n_harmonized)
        checked = 0
        for s_row in rows:
            if s_row.onset_date and s_row.end_date and not s_row.is_partial:
                window = sum(
                    float(r.n_harmonized) for r in hd
                    if s_row.onset_date <= r.local_date <= s_row.end_date)
                if by_year.get(s_row.year, 0.0) > 0:
                    assert window >= 0.799 * by_year[s_row.year]
                    checked += 1
        assert checked > 0


def select_season_metrics():
    from sqlalchemy import select
    from app.models import SeasonMetrics
    return select(SeasonMetrics)
