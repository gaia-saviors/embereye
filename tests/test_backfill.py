"""Backfill correctness: SP/NRT seam windows, availability parsing, state
invalidation, NRT schema handling and static-source location matching.

Run: cd backend && python -m pytest tests/test_backfill.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_backend = _ROOT / "backend"
if _backend.exists() and str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("EMBEREBEYE_DATABASE_URL", "sqlite:///./test_embereye.db")

# Import the script as a module (it lives in scripts/backfill_firms.py).
_SCRIPT = _ROOT / "scripts" / "backfill_firms.py"
if not _SCRIPT.exists():
    for candidate in [Path("/srv/scripts/backfill_firms.py"), Path("/app/scripts/backfill_firms.py")]:
        if candidate.exists():
            _SCRIPT = candidate
            break
_spec = importlib.util.spec_from_file_location("backfill_firms", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
sys.modules["backfill_firms"] = bf
_spec.loader.exec_module(bf)


def _avail() -> bf.Availability:
    return bf.Availability(dict(bf.FALLBACK_AVAILABILITY), "fallback", "test")


# --------------------------------------------------------------------------
# acq_time: SP drops leading zeros (1-4 digits), NRT is HHMM
# --------------------------------------------------------------------------
class TestAcqTime:
    def test_sp_unpadded_forms(self):
        from app.ingest.canonical import parse_acq_time

        assert parse_acq_time("0") == (0, 0)
        assert parse_acq_time("30") == (0, 30)
        assert parse_acq_time("850") == (8, 50)
        assert parse_acq_time("2359") == (23, 59)

    def test_nrt_and_six_digit_still_work(self):
        from app.ingest.canonical import parse_acq_time

        assert parse_acq_time("0525") == (5, 25)
        assert parse_acq_time("052501") == (5, 25)
        with pytest.raises(Exception):
            parse_acq_time("2560")
        with pytest.raises(Exception):
            parse_acq_time("12345")          # ambiguous 5 digits


# --------------------------------------------------------------------------
# parser: SP has `type`, NRT does not; product tagging
# --------------------------------------------------------------------------
VIIRS_SP_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight,type\n"
    "30.9451,31.2503,344.2,0.42,0.41,2026-07-15,1234,SNPP,VIIRS,h,2.0QF,292.1,3.5,D,0\n"
    "30.9912,30.8874,351.8,0.44,0.40,2026-07-15,1235,SNPP,VIIRS,h,2.0QF,295.0,6.1,D,2\n"
)
VIIRS_NRT_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight\n"
    "30.9915,30.8870,350.1,0.44,0.41,2026-09-05,0,SUOMI-NPP,VIIRS,n,2.0NRT,293.4,4.2,N\n"
)


class TestParserProductSchemas:
    def test_sp_rows_carry_type_and_are_tagged_sp(self):
        from app.ingest.parser import parse_firms_csv

        recs, rej, total, _ = parse_firms_csv(VIIRS_SP_CSV, "aoi", "VIIRS", product="SP")
        assert (len(recs), rej, total) == (2, 0, 2)
        assert all(r.firms_product == "SP" for r in recs)
        assert recs[0].firms_type == 0 and recs[0].static_source is False
        assert recs[1].firms_type == 2 and recs[1].static_source is True

    def test_nrt_rows_have_null_type_and_are_tagged_nrt(self):
        from app.ingest.parser import parse_firms_csv

        recs, rej, total, _ = parse_firms_csv(VIIRS_NRT_CSV, "aoi", "VIIRS", product="NRT")
        assert (len(recs), rej, total) == (1, 0, 1)
        rec = recs[0]
        assert rec.firms_product == "NRT"
        assert rec.firms_type is None
        assert rec.source_version == "2.0NRT"
        assert rec.acq_dt_utc.hour == 0 and rec.acq_dt_utc.minute == 0  # "0" -> 00:00

    def test_bad_type_token_is_rejected(self):
        from app.ingest.parser import parse_firms_csv

        bad = VIIRS_SP_CSV.replace("2026-07-15,1234,SNPP,VIIRS,h,2.0QF,292.1,3.5,D,0",
                                   "2026-07-15,1234,SNPP,VIIRS,h,2.0QF,292.1,3.5,D,X")
        _recs, rej, total, _ = parse_firms_csv(bad, "aoi", "VIIRS", product="SP")
        assert (rej, total) == (1, 2)


# --------------------------------------------------------------------------
# availability CSV parsing (tolerant) + fallback
# --------------------------------------------------------------------------
class TestAvailability:
    def test_tolerant_column_orders(self):
        csv = (
            "product_id,date_min,date_max,extra\n"
            "MODIS_SP,2000-11-01,2026-08-31,x\n"
            "MODIS_NRT,2026-09-01,2026-11-01,x\n"
            "VIIRS_SNPP_SP,2012-01-20,2026-07-31,x\n"
        )
        out = bf.parse_availability_csv(csv)
        assert out["MODIS_SP"] == (date(2000, 11, 1), date(2026, 8, 31))
        assert out["MODIS_NRT"] == (date(2026, 9, 1), date(2026, 11, 1))
        assert out["VIIRS_SNPP_SP"] == (date(2012, 1, 20), date(2026, 7, 31))

    def test_header_and_noise_rows_skipped(self):
        csv = "note,foo,bar\nMODIS_NRT,some,2026-09-01,2026-11-01\n"
        out = bf.parse_availability_csv(csv)
        assert out["MODIS_NRT"] == (date(2026, 9, 1), date(2026, 11, 1))

    def test_non_csv_body_raises(self):
        with pytest.raises(bf.BackfillError):
            bf.parse_availability_csv("Invalid MAP_KEY. Check your FIRMS account.")
        with pytest.raises(bf.BackfillError):
            bf.parse_availability_csv("")

    def test_fallback_table_matches_verified_response(self):
        a = _avail()
        assert a.max_date("MODIS_SP") == date(2026, 8, 31)
        assert a.min_date("MODIS_NRT") == date(2026, 9, 1)
        assert a.max_date("VIIRS_SNPP_SP") == date(2026, 7, 31)
        assert a.min_date("VIIRS_SNPP_NRT") == date(2026, 8, 1)
        assert a.max_date("VIIRS_NOAA20_SP") == date(2026, 7, 31)
        assert a.min_date("VIIRS_NOAA21_NRT") == date(2024, 1, 17)


# --------------------------------------------------------------------------
# windows: SP up to max_date, NRT from the day after, never SP-to-today
# --------------------------------------------------------------------------
class TestWindows:
    def test_seam_is_day_after_sp_max(self):
        plan = bf.build_windows("MODIS", date(2012, 1, 1), date(2026, 10, 31), _avail())
        assert plan.sp.product == "MODIS_SP"
        assert plan.sp.end == date(2026, 8, 31)          # SP max, not end of window
        assert plan.nrt.product == "MODIS_NRT"
        assert plan.nrt.start == date(2026, 9, 1)        # day after SP max
        assert plan.nrt.end == date(2026, 10, 31)
        assert any("clamped to SP max_date" in n for n in plan.notes)

    def test_snpp_seam(self):
        plan = bf.build_windows("SNPP", date(2012, 1, 1), date(2026, 10, 31), _avail())
        assert plan.sp.end == date(2026, 7, 31)
        assert plan.nrt.start == date(2026, 8, 1)

    def test_noaa21_nrt_min_does_not_extend_fetch_back(self):
        # NRT availability starts 2024-01-17, but the seam stays at
        # SP max_date + 1: SP wins inside the overlap.
        plan = bf.build_windows("NOAA21", date(2023, 3, 1), date(2026, 10, 31), _avail())
        assert plan.sp.start == date(2023, 3, 1)
        assert plan.nrt.start == date(2026, 8, 1)

    def test_start_clamped_to_product_start(self):
        plan = bf.build_windows("SNPP", date(2010, 1, 1), date(2026, 10, 31), _avail())
        assert plan.sp.start == date(2012, 1, 20)
        assert any("clamped to product availability" in n for n in plan.notes)

    def test_recent_only_window_skips_sp(self):
        plan = bf.build_windows("MODIS", date(2026, 10, 1), date(2026, 10, 31), _avail())
        assert plan.sp is None
        assert plan.nrt.start == date(2026, 10, 1)
        assert any("no SP fetch" in n for n in plan.notes)

    def test_chunk_math(self):
        chunks = bf.iter_chunks(date(2026, 9, 1), date(2026, 10, 31), 5)
        assert len(chunks) == 13
        assert chunks[0] == (date(2026, 9, 1), date(2026, 9, 5))
        assert chunks[-1] == (date(2026, 10, 31), date(2026, 10, 31))
        covered = sum((e - s).days + 1 for s, e in chunks)
        assert covered == 61                      # full coverage, no gaps/overlaps

    def test_five_day_chunks_start_aligned(self):
        # With the challenge-scope start the first chunk begins exactly at
        # the window start and day-alignment is deterministic.
        chunks = bf.iter_chunks(bf.DEFAULT_START, date(2026, 8, 31), 5)
        assert chunks[0] == (bf.DEFAULT_START, date(2000, 11, 5))
        assert covered_all(chunks) == 9435

    def test_chunk_days_capped_at_five(self):
        argv = ["--dry-run", "--no-db", "--chunk-days", "10"]
        with pytest.raises(SystemExit):
            bf.parse_args(argv)

    def test_request_count(self):
        plans, total = bf.build_plans(
            ["SNPP", "NOAA20", "NOAA21", "MODIS"],
            bf.DEFAULT_START, date(2026, 10, 31), _avail(), 5)
        per = {p.sensor: sum(w.n_chunks for w in p.windows) for p in plans}
        assert total == sum(per.values())
        # Exact arithmetic (fallback availability, official 5-day chunks):
        assert per["MODIS"] == 1887 + 13      # SP 2000-11-01..2026-08-31 + NRT
        assert per["SNPP"] == 1062 + 19
        assert per["NOAA20"] == 609 + 19
        assert per["NOAA21"] == 250 + 19
        assert total == 3878


def covered_all(chunks) -> int:
    return sum((e - s).days + 1 for s, e in chunks)


# --------------------------------------------------------------------------
# fetch order: SNPP/N20/N21 SP->NRT; MODIS NRT first, SP newest -> oldest
# --------------------------------------------------------------------------
class TestFetchOrder:
    def test_modis_nrt_first_then_sp_newest_to_oldest(self):
        plans, _ = bf.build_plans(["MODIS"], bf.DEFAULT_START,
                                  date(2026, 10, 31), _avail(), 5)
        seq = bf.fetch_sequence(plans[0])
        assert [w.product for w, _nf in seq] == ["MODIS_NRT", "MODIS_SP"]
        assert seq[0][1] is False                    # NRT oldest -> newest
        assert seq[1][1] is True                     # SP newest -> oldest

    def test_viirs_sensors_sp_then_nrt(self):
        plans, _ = bf.build_plans(["SNPP", "NOAA20", "NOAA21"], bf.DEFAULT_START,
                                  date(2026, 10, 31), _avail(), 5)
        for plan in plans:
            seq = bf.fetch_sequence(plan)
            assert [w.product for w, _nf in seq] == [
                f"VIIRS_{plan.sensor}_SP", f"VIIRS_{plan.sensor}_NRT"]
            assert all(nf is False for _w, nf in seq)

    def test_reversed_chunks_preserve_boundaries(self):
        fwd = bf.iter_chunks(date(2026, 5, 1), date(2026, 6, 15), 5)
        rev = list(reversed(fwd))
        assert set(fwd) == set(rev)
        assert rev[0][1] == date(2026, 6, 15)        # newest chunk first


# --------------------------------------------------------------------------
# chunk URL: forward-anchored at c_start, DAY_RANGE = window days
# --------------------------------------------------------------------------
class TestChunkUrl:
    class _Cfg:
        firms_base_url = "https://firms.modaps.eosdis.nasa.gov"
        firms_map_key = "KEY123"

    def test_anchored_at_start_not_end(self):
        url = bf.chunk_url(self._Cfg(), "MODIS_SP", "30.0,29.0,31.0,32.0",
                           date(2026, 8, 27), date(2026, 8, 31))
        # docs: [DATE .. DATE + DAY_RANGE - 1] -> DATE must be the chunk START
        assert url.endswith("/5/2026-08-27")
        assert "MODIS_SP" in url and "KEY123" in url

    def test_one_day_chunk(self):
        url = bf.chunk_url(self._Cfg(), "VIIRS_SNPP_NRT", "b",
                           date(2026, 10, 1), date(2026, 10, 1))
        assert url.endswith("/1/2026-10-01")


# --------------------------------------------------------------------------
# per-chunk sanity: rows outside window + truncation hint + signatures
# --------------------------------------------------------------------------
def _rec(acq: date, lat: float = 30.0, lon: float = 31.0):
    from app.ingest.canonical import CanonicalRecord

    return CanonicalRecord(
        aoi_id="a", sensor_family="VIIRS", satellite="SNPP", instrument="VIIRS",
        resolution_m=375, acq_dt_utc=datetime(acq.year, acq.month, acq.day, 12, 0),
        acq_date=acq, local_date=acq, lat=lat, lon=lon, brightness_t4_k=340.0,
        brightness_t11_k=300.0, frp_mw=1.0, confidence_class="HIGH",
        confidence_raw=None, daynight="D", scan_km=0.4, track_km=0.4,
        pixel_area_km2=0.16, saturated=False, band21_fallback=False)


class TestChunkSanity:
    W0, W4 = date(2026, 9, 1), date(2026, 9, 5)    # 5-day window

    def test_rows_inside_window_are_not_flagged(self):
        recs = [_rec(self.W0), _rec(date(2026, 9, 3)), _rec(self.W4)]
        assert bf.rows_outside_window(recs, self.W0, self.W4) == []

    def test_rows_outside_window_detected(self):
        recs = [_rec(self.W0), _rec(date(2026, 8, 31)), _rec(date(2026, 9, 6))]
        assert bf.rows_outside_window(recs, self.W0, self.W4) == \
            [date(2026, 8, 31), date(2026, 9, 6)]

    def test_truncation_hint_short_span_at_end(self):
        recs = [_rec(date(2026, 9, 4)), _rec(self.W4)]
        hint = bf.truncation_hint(recs, self.W0, self.W4)
        assert hint is not None and "truncated window start" in hint

    def test_no_hint_for_full_span(self):
        recs = [_rec(d) for d in (self.W0, date(2026, 9, 2), date(2026, 9, 3),
                                  date(2026, 9, 4), self.W4)]
        assert bf.truncation_hint(recs, self.W0, self.W4) is None

    def test_no_hint_without_rows_near_end(self):
        # episodic fire in the middle only: normal, never a hint
        recs = [_rec(date(2026, 9, 2)), _rec(date(2026, 9, 3))]
        assert bf.truncation_hint(recs, self.W0, self.W4) is None

    def test_row_signature_order_independent(self):
        a = [_rec(self.W0, 30.1, 31.1), _rec(self.W4, 30.2, 31.2)]
        b = [_rec(self.W4, 30.2, 31.2), _rec(self.W0, 30.1, 31.1)]
        assert bf.row_signature(a) == bf.row_signature(b)


# --------------------------------------------------------------------------
# state: legacy migration + out-of-coverage invalidation
# --------------------------------------------------------------------------
class TestStateInvalidation:
    def _write_legacy(self, tmp_path: Path) -> Path:
        legacy = {
            "MODIS_SP": {
                "2026-08-22": {"end": "2026-08-31", "rows": 420, "status": "ok"},
                # old bug: SP requested past its archive, header-only, 0 rows
                "2026-09-01": {"end": "2026-09-10", "rows": 0, "status": "ok"},
                "2026-09-11": {"end": "2026-09-20", "rows": 0, "status": "ok"},
            },
            "MODIS_NRT": {
                "2026-10-01": {"end": "2026-10-10", "rows": 88, "status": "ok"},
                # NRT before its min_date is out of coverage too
                "2026-08-01": {"end": "2026-08-10", "rows": 0, "status": "ok"},
            },
        }
        p = tmp_path / "state.json"
        p.write_text(json.dumps(legacy))
        return p

    def test_sp_beyond_max_invalidated_even_with_zero_rows(self, tmp_path):
        p = self._write_legacy(tmp_path)
        state = bf.load_state(p, "nile-delta-egypt", _avail(), dry_run=True)
        sp = state.chunks["MODIS_SP"]
        assert "2026-08-22" in sp                          # in-coverage kept
        assert "2026-09-01" not in sp and "2026-09-11" not in sp
        r = state.report
        assert r["invalidated_sp_beyond_max"] == 2
        assert r["invalidated_sp_beyond_max_zero_rows"] == 2
        assert r["migrated"] == 5

    def test_nrt_before_min_invalidated(self, tmp_path):
        p = self._write_legacy(tmp_path)
        state = bf.load_state(p, "nile-delta-egypt", _avail(), dry_run=True)
        nrt = state.chunks["MODIS_NRT"]
        assert "2026-10-01" in nrt
        assert "2026-08-01" not in nrt
        assert state.report["invalidated_nrt_before_min"] == 1

    def test_dry_run_does_not_write(self, tmp_path):
        p = self._write_legacy(tmp_path)
        before = p.read_text()
        bf.load_state(p, "nile-delta-egypt", _avail(), dry_run=True)
        assert p.read_text() == before

    def test_persisted_v2_state_roundtrip(self, tmp_path):
        p = tmp_path / "state.json"
        state = bf.BackfillState(aoi_id="aoi")
        state.mark("MODIS_SP", date(2026, 8, 22), date(2026, 8, 31), 420, "OK", 1)
        bf.save_state(state, p, _avail())
        loaded = bf.load_state(p, "aoi", _avail(), dry_run=True)
        assert loaded.report["source"] == "v2"
        assert loaded.done("MODIS_SP", date(2026, 8, 22), date(2026, 8, 31))

    def test_legacy_zero_row_incoverage_chunk_is_kept(self, tmp_path):
        # A 0-row chunk INSIDE coverage is a legitimate quiet window: kept.
        legacy = {"VIIRS_NOAA20_SP": {
            "2026-06-01": {"end": "2026-06-10", "rows": 0, "status": "ok"},
        }}
        p = tmp_path / "s.json"
        p.write_text(json.dumps(legacy))
        state = bf.load_state(p, "aoi", _avail(), dry_run=True)
        assert "2026-06-01" in state.chunks["VIIRS_NOAA20_SP"]
        assert state.report["invalidated_sp_beyond_max"] == 0


# --------------------------------------------------------------------------
# non-CSV guard (Invalid MAP_KEY, rate-limit text, HTML)
# --------------------------------------------------------------------------
class TestNonCsvGuard:
    def test_valid_header_only_csv_passes(self):
        bf.ensure_firms_csv("u", 200,
                            "latitude,longitude,bright_ti4,acq_date,acq_time\n")

    def test_invalid_map_key_raises_and_prints_body(self):
        with pytest.raises(bf.FetchError) as ei:
            bf.ensure_firms_csv("http://x", 200, "Invalid MAP_KEY. Get one at firms.")
        assert "Invalid MAP_KEY" in str(ei.value)
        assert "http://x" in str(ei.value)

    def test_rate_limit_text_raises(self):
        with pytest.raises(bf.FetchError):
            bf.ensure_firms_csv("u", 429, "Too Many Requests: slow down")

    def test_html_raises(self):
        with pytest.raises(bf.FetchError):
            bf.ensure_firms_csv("u", 200, "<html><body>error</body></html>")


# --------------------------------------------------------------------------
# static-source locations: SP type=2 list applied to NRT rows
# --------------------------------------------------------------------------
class TestStaticLocations:
    def _recs(self, rows):
        from app.ingest.parser import parse_firms_csv

        header = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,"
                  "satellite,instrument,confidence,version,bright_ti5,frp,daynight\n")
        body = "".join(
            f"{lat},{lon},350.0,0.4,0.4,{d},{t},SUOMI-NPP,VIIRS,n,2.0NRT,293.0,4.0,N\n"
            for lat, lon, d, t in rows)
        return parse_firms_csv(header + body, "aoi", "VIIRS", product="NRT")[0]

    def test_sp_locations_collected_and_nrt_matched(self):
        # SP row with type=2 at the flare site (from the SP CSV fixture)
        from app.ingest.parser import parse_firms_csv

        sp_recs = parse_firms_csv(VIIRS_SP_CSV, "aoi", "VIIRS", product="SP")[0]
        locs: set = set()
        assert bf.add_sp_static_locations(sp_recs, locs) == 1
        assert (round(30.9912, 2), round(30.8874, 2)) in locs

        nrt = self._recs([("30.9915", "30.8870", "2026-09-05", "0"),
                          ("31.4000", "30.5000", "2026-09-05", "30")])
        hits = bf.apply_static_locations(nrt, locs)
        assert hits == 1
        assert nrt[0].static_source is True
        assert nrt[0].firms_type is None             # type stays NULL for NRT
        assert nrt[1].static_source is False

    def test_db_seed_rounds_identically(self):
        # db_static_locations must round the same way as add_sp_static_locations
        lat, lon = 30.991234, 30.887456
        assert (round(lat, 2), round(lon, 2)) == (30.99, 30.89)


# ==========================================================================
# v1.0.4: DB plumbing - fail-loudly resolution, scoped --fresh, raw copies,
# --status and the seeder real-data guard.
# ==========================================================================
class TestDbResolution:
    def test_env_url_is_used(self, monkeypatch):
        monkeypatch.setenv("EMBEREBEYE_DATABASE_URL",
                           "postgresql+psycopg://u:p@localhost:5432/embereye")
        monkeypatch.delenv("EMBEREBEYE_DB_URL", raising=False)
        url, source = bf.resolve_db_url(None)
        assert url.startswith("postgresql+psycopg://")
        assert source == "EMBEREBEYE_DATABASE_URL"

    def test_alias_env_is_used(self, monkeypatch):
        monkeypatch.delenv("EMBEREBEYE_DATABASE_URL", raising=False)
        monkeypatch.setenv("EMBEREBEYE_DB_URL", "sqlite:///./x.db")
        url, source = bf.resolve_db_url(None)
        assert url == "sqlite:///./x.db" and source == "EMBEREBEYE_DB_URL"

    def test_sqlite_flag_beats_env_and_is_absolute(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EMBEREBEYE_DATABASE_URL", "postgresql://ignored")
        url, source = bf.resolve_db_url(str(tmp_path / "real.db"))
        assert url == f"sqlite:///{(tmp_path / 'real.db').as_posix()}"
        assert source.startswith("--sqlite")

    def test_no_db_raises_with_fix_instructions(self, monkeypatch):
        monkeypatch.delenv("EMBEREBEYE_DATABASE_URL", raising=False)
        monkeypatch.delenv("EMBEREBEYE_DB_URL", raising=False)
        with pytest.raises(bf.BackfillError) as exc:
            bf.resolve_db_url(None)
        assert "EMBEREBEYE_DATABASE_URL" in str(exc.value)
        assert "--sqlite" in str(exc.value)

    def test_describe_url_masks_password(self):
        from app.db import describe_url, mask_url

        url = "postgresql+psycopg://embereye:secretpw@localhost:5432/embereye"
        assert "secretpw" not in describe_url(url)
        assert "secretpw" not in mask_url(url)
        assert "localhost:5432" in describe_url(url)
        assert "embereye" in describe_url(url)

    def test_describe_url_sqlite_paths(self):
        from app.db import describe_url

        assert describe_url("sqlite:////tmp/real.db") == "sqlite file: /tmp/real.db"
        assert "embereye_dev.db" in describe_url("sqlite:///./embereye_dev.db")


class TestFreshScoping:
    def test_satellites_of_sensors(self):
        assert bf.satellites_of(["NOAA20", "NOAA21"]) == {"N20", "N21"}
        assert bf.satellites_of(["SNPP"]) == {"SNPP"}
        assert bf.satellites_of(["MODIS"]) == {"Terra", "Aqua"}
        assert bf.satellites_of(bf.DEFAULT_SENSOR_ORDER) == \
            {"Terra", "Aqua", "SNPP", "N20", "N21"}

    def test_drop_sensor_products_is_scoped(self):
        state = bf.BackfillState(aoi_id="aoi")
        state.chunks = {
            "VIIRS_SNPP_SP": {"2026-01-01": {"end": "2026-01-05"}},
            "VIIRS_SNPP_NRT": {"2026-08-01": {"end": "2026-08-05"}},
            "VIIRS_NOAA20_SP": {"2026-01-01": {"end": "2026-01-05"}},
            "MODIS_SP": {"2012-01-01": {"end": "2012-01-05"}},
        }
        removed = bf.drop_sensor_products(state, ["NOAA20"])
        assert removed == 1
        assert "VIIRS_NOAA20_SP" not in state.chunks
        # the accident scenario: S-NPP state must survive a NOAA-only fresh
        assert "VIIRS_SNPP_SP" in state.chunks
        assert "MODIS_SP" in state.chunks

    def test_confirm_destructive_paths(self, monkeypatch):
        assert bf.confirm_destructive("delete 5 rows?", assume_yes=True) is True
        monkeypatch.setattr("builtins.input", lambda _: "YES")
        assert bf.confirm_destructive("delete 5 rows?", assume_yes=False) is True
        monkeypatch.setattr("builtins.input", lambda _: "no")
        assert bf.confirm_destructive("delete 5 rows?", assume_yes=False) is False

        def _eof(_):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        assert bf.confirm_destructive("delete 5 rows?", assume_yes=False) is False


class TestRawSaving:
    def test_raw_layout_and_idempotence(self, tmp_path):
        raw_dir = tmp_path / "raw"
        assert bf.raw_path_for(raw_dir, "VIIRS_SNPP_SP", date(2026, 1, 1),
                               date(2026, 1, 5)) == (
            raw_dir / "VIIRS_SNPP_SP" / "2026-01-01_2026-01-05.csv")
        body = "latitude,longitude\n30.9,30.8\n"
        assert bf.save_raw_body(raw_dir, "VIIRS_SNPP_SP",
                                date(2026, 1, 1), date(2026, 1, 5), body) is True
        first = (raw_dir / "VIIRS_SNPP_SP" / "2026-01-01_2026-01-05.csv")
        assert first.read_text() == body
        # a resumed/second write must not overwrite the first copy
        assert bf.save_raw_body(raw_dir, "VIIRS_SNPP_SP",
                                date(2026, 1, 1), date(2026, 1, 5),
                                "TAMPERED") is False
        assert first.read_text() == body


def _rebind_app_db(url: str):
    """Point the app package at a fresh database for one test class."""
    os.environ["EMBEREBEYE_DATABASE_URL"] = url
    os.environ.pop("EMBEREBEYE_DB_URL", None)
    for m in list(sys.modules):
        if m.startswith("app"):
            del sys.modules[m]
    from app.config import get_settings

    get_settings.cache_clear()


class TestStatusCommand:
    def test_status_reports_rows_and_types(self, tmp_path, capsys):
        db_path = tmp_path / "status.db"
        _rebind_app_db(f"sqlite:///{db_path.as_posix()}")
        from app.db import SessionLocal, create_all
        from app.ingest.persist import create_run, persist_records
        from app.ingest.canonical import CanonicalRecord
        from datetime import datetime as _dt

        create_all()
        db = SessionLocal()
        from app.seed.pipeline import ensure_preset_aois

        ensure_preset_aois(db)
        run = create_run(db, mode="LIVE", source="firms_backfill",
                         aoi_id="nile-delta-egypt")
        recs = []
        for i, (sat, prod, ftype, st) in enumerate([
                ("Terra", "SP", 2, True), ("SNPP", "NRT", None, False),
                ("SNPP", "NRT", None, True), ("N20", "SP", 0, False)]):
            recs.append(CanonicalRecord(
                aoi_id="nile-delta-egypt", sensor_family="MODIS" if sat == "Terra" else "VIIRS",
                satellite=sat, instrument="MODIS" if sat == "Terra" else "VIIRS",
                resolution_m=1000 if sat == "Terra" else 375,
                acq_dt_utc=_dt(2026, 1, 1 + i, 10, 30), acq_date=date(2026, 1, 1 + i),
                local_date=date(2026, 1, 1 + i), lat=30.9, lon=30.8,
                brightness_t4_k=330.0, brightness_t11_k=300.0, frp_mw=5.0,
                confidence_class="NOMINAL", confidence_raw=None, daynight="D",
                scan_km=1.0, track_km=1.0, pixel_area_km2=1.0, saturated=False,
                band21_fallback=False, static_source=st, source_version="firms",
                firms_product=prod, firms_type=ftype, run_id=run.id))
        persist_records(db, run.id, recs)
        db.close()

        args = type("A", (), {"sqlite": None})()
        rc = bf.cmd_status(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Per satellite" in out
        for token in ("Terra", "SNPP", "N20"):
            assert token in out
        assert "Database status" in out

    def test_status_empty_db_notes_it(self, tmp_path, capsys):
        db_path = tmp_path / "empty.db"
        _rebind_app_db(f"sqlite:///{db_path.as_posix()}")
        args = type("A", (), {"sqlite": None})()
        rc = bf.cmd_status(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "NO hotspots" in out


class TestSeederRealDataGuard:
    def test_refuses_when_real_rows_present(self, tmp_path, monkeypatch):
        db_path = tmp_path / "guard.db"
        _rebind_app_db(f"sqlite:///{db_path.as_posix()}")
        os.environ["SEED_SCALE"] = "0.02"
        monkeypatch.delenv("FORCE_SEED", raising=False)
        monkeypatch.delenv("EMBEREBEYE_ALLOW_SEED_OVER_REAL", raising=False)
        from app.db import SessionLocal, create_all
        from app.ingest.persist import create_run, persist_records
        from app.ingest.canonical import CanonicalRecord
        from app.models import Hotspot
        from app.seed.pipeline import run_seed

        create_all()
        db = SessionLocal()
        from app.seed.pipeline import ensure_preset_aois

        ensure_preset_aois(db)
        run = create_run(db, mode="LIVE", source="firms_backfill",
                         aoi_id="nile-delta-egypt")
        persist_records(db, run.id, [CanonicalRecord(
            aoi_id="nile-delta-egypt", sensor_family="VIIRS", satellite="SNPP",
            instrument="VIIRS", resolution_m=375,
            acq_dt_utc=datetime(2026, 1, 1, 10, 30), acq_date=date(2026, 1, 1),
            local_date=date(2026, 1, 1), lat=30.9, lon=30.8,
            brightness_t4_k=330.0, brightness_t11_k=300.0, frp_mw=5.0,
            confidence_class="NOMINAL", confidence_raw=None, daynight="D",
            scan_km=1.0, track_km=1.0, pixel_area_km2=1.0, saturated=False,
            band21_fallback=False, static_source=False, source_version="firms",
            firms_product="SP", firms_type=0, run_id=run.id)])
        n_before = db.query(Hotspot).count()
        db.close()

        report = run_seed(force=True)          # even force must refuse
        assert report["status"] == "REFUSED"
        assert report["reason"] == "real-data-present"
        db = SessionLocal()
        assert db.query(Hotspot).count() == n_before
        db.close()

    def test_override_replaces_when_double_explicit(self, tmp_path, monkeypatch):
        db_path = tmp_path / "override.db"
        _rebind_app_db(f"sqlite:///{db_path.as_posix()}")
        os.environ["SEED_SCALE"] = "0.02"
        monkeypatch.setenv("FORCE_SEED", "1")
        monkeypatch.setenv("EMBEREBEYE_ALLOW_SEED_OVER_REAL", "1")
        from app.db import SessionLocal, create_all
        from app.ingest.persist import create_run, persist_records
        from app.ingest.canonical import CanonicalRecord
        from app.models import Hotspot
        from app.seed.pipeline import run_seed

        create_all()
        db = SessionLocal()
        from app.seed.pipeline import ensure_preset_aois

        ensure_preset_aois(db)
        run = create_run(db, mode="LIVE", source="firms_backfill",
                         aoi_id="nile-delta-egypt")
        persist_records(db, run.id, [CanonicalRecord(
            aoi_id="nile-delta-egypt", sensor_family="VIIRS", satellite="SNPP",
            instrument="VIIRS", resolution_m=375,
            acq_dt_utc=datetime(2026, 1, 1, 10, 30), acq_date=date(2026, 1, 1),
            local_date=date(2026, 1, 1), lat=30.9, lon=30.8,
            brightness_t4_k=330.0, brightness_t11_k=300.0, frp_mw=5.0,
            confidence_class="NOMINAL", confidence_raw=None, daynight="D",
            scan_km=1.0, track_km=1.0, pixel_area_km2=1.0, saturated=False,
            band21_fallback=False, static_source=False, source_version="firms",
            firms_product="SP", firms_type=0, run_id=run.id)])
        db.close()

        report = run_seed(force=True)          # double-explicit override wins
        assert report["status"] == "OK"
        db = SessionLocal()
        rows = db.query(Hotspot).all()
        assert rows, "override reseeds the archive"
        assert all(r.firms_product in ("", None) for r in rows)
        db.close()
