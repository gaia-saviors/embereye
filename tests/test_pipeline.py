"""Golden-file + determinism test suite.

Run: cd backend && python -m pytest tests -q
Covers: canonicalization, dedup rule, geometry, transfer-fit recovery of a
known power law, seam weights, climatology z-scores, episode determinism,
synthetic-archive determinism, and API contract smoke.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone

import numpy as np
import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend = os.path.join(_repo_root, "backend")
if os.path.exists(_backend) and _backend not in sys.path:
    sys.path.insert(0, _backend)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

os.environ.setdefault("EMBEREBEYE_DATABASE_URL", "sqlite:///./test_embereye.db")


# --------------------------------------------------------------------------
# canonicalization
# --------------------------------------------------------------------------
class TestCanonicalization:
    def test_modis_numeric_confidence_bands(self):
        from app.ingest.canonical import ordinalize_confidence

        assert ordinalize_confidence("29", "MODIS")[0] == "LOW"
        assert ordinalize_confidence("30", "MODIS")[0] == "NOMINAL"
        assert ordinalize_confidence("79", "MODIS")[0] == "NOMINAL"
        assert ordinalize_confidence("80", "MODIS")[0] == "HIGH"
        assert ordinalize_confidence("65", "MODIS")[1] == 65.0

    def test_viirs_categorical_never_fabricates_numbers(self):
        from app.ingest.canonical import ordinalize_confidence

        cls, raw = ordinalize_confidence("h", "VIIRS")
        assert (cls, raw) == ("HIGH", None)

    def test_rejects_bad_confidence(self):
        from app.ingest.canonical import RecordRejected, ordinalize_confidence

        with pytest.raises(RecordRejected):
            ordinalize_confidence("x9", "MODIS")

    def test_local_calendar_day_bucketing(self):
        from app.ingest.canonical import local_calendar_day

        # lon=30E -> +2h: 23:00 UTC Jan 1 is already Jan 2 local
        dt = datetime(2023, 1, 1, 23, 0, tzinfo=None)
        assert local_calendar_day(dt, 30.0) == date(2023, 1, 2)
        # lon=-120W -> -8h: 05:00 UTC Jan 2 is still Jan 1 local
        dt2 = datetime(2023, 1, 2, 5, 0)
        assert local_calendar_day(dt2, -120.0) == date(2023, 1, 1)

    def test_acq_time_parsing(self):
        from app.ingest.canonical import parse_acq_time

        assert parse_acq_time("0525") == (5, 25)
        assert parse_acq_time("052501") == (5, 25)
        with pytest.raises(Exception):
            parse_acq_time("99")
        with pytest.raises(Exception):
            parse_acq_time("2560")

    def test_satellite_aliases(self):
        from app.ingest.canonical import canonical_satellite

        assert canonical_satellite("T") == "Terra"
        assert canonical_satellite("A") == "Aqua"
        assert canonical_satellite("N20") == "N20"
        assert canonical_satellite("NOAA-20") == "N20"
        with pytest.raises(Exception):
            canonical_satellite("X1")


# --------------------------------------------------------------------------
# dedup (Zheng 2026 adaptation)
# --------------------------------------------------------------------------
def _mk(i, lat, lon, dt, frp, sensor="VIIRS", sat="SNPP", conf="HIGH", raw=None):
    from app.ingest.canonical import CanonicalRecord

    return CanonicalRecord(
        aoi_id="t", sensor_family=sensor, satellite=sat, instrument=sensor,
        resolution_m=375 if sensor == "VIIRS" else 1000,
        acq_dt_utc=dt, acq_date=dt.date(),
        local_date=dt.date(), lat=lat, lon=lon,
        brightness_t4_k=340.0, brightness_t11_k=300.0, frp_mw=frp,
        confidence_class=conf, confidence_raw=raw, daynight="D",
        scan_km=0.5, track_km=0.5, pixel_area_km2=0.25,
        saturated=False, band21_fallback=False,
    )


class TestDedup:
    def test_removes_sub_pixel_repeat_keeps_highest_confidence(self):
        from app.ingest.dedup import deduplicate

        dt = datetime(2023, 1, 1, 10, 0)
        recs = [
            _mk(1, 30.000, 31.000, dt, 10.0, conf="LOW"),
            _mk(2, 30.0005, 31.0005, dt, 12.0, conf="HIGH"),  # ~70 m away
            _mk(3, 30.004, 31.004, dt, 20.0, conf="NOMINAL"),  # ~550 m: VIIRS keeps
        ]
        kept, n = deduplicate(recs, max_distance_km_viirs=0.2)
        assert n == 1
        assert len(kept) == 2
        rep = [r for r in kept if abs(r.lat - 30.000) < 1e-3][0]
        assert rep.confidence_class == "HIGH"
        assert rep.frp_mw == pytest.approx(11.0)  # cluster mean

    def test_modis_threshold_wider(self):
        from app.ingest.dedup import deduplicate

        dt = datetime(2023, 1, 1, 10, 0)
        recs = [
            _mk(1, 30.000, 31.000, dt, 10.0, sensor="MODIS", sat="Aqua"),
            _mk(2, 30.004, 31.004, dt, 20.0, sensor="MODIS", sat="Aqua"),
        ]
        kept, n = deduplicate(recs, max_distance_km_modis=0.8)
        assert n == 1 and len(kept) == 1

    def test_order_independence(self):
        from app.ingest.dedup import deduplicate

        dt = datetime(2023, 1, 1, 10, 0)
        recs = [_mk(i, 30.0 + i * 1e-4, 31.0, dt, 5.0 + i) for i in range(6)]
        a, na = deduplicate(list(reversed(recs)))
        b, nb = deduplicate(recs)
        assert na == nb
        assert [(r.lat, r.frp_mw) for r in a] == [(r.lat, r.frp_mw) for r in b]


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
class TestGeometry:
    def test_haversine_known_distance(self):
        from app.geometry import haversine_km

        d = haversine_km(np.array([0.0]), np.array([0.0]), np.array([0.0]), np.array([1.0]))
        assert abs(d[0] - 111.195) < 0.1

    def test_point_in_polygon(self):
        from app.geometry import points_in_polygon

        square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        inside = points_in_polygon(np.array([5.0, 11.0, 10.0, -1.0]),
                                   np.array([5.0, 5.0, 10.0, 5.0]), square)
        assert inside.tolist() == [True, False, True, False]

    def test_polygon_centroid(self):
        from app.geometry import polygon_centroid

        cx, cy = polygon_centroid([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
        assert (cx, cy) == pytest.approx((5.0, 5.0))


# --------------------------------------------------------------------------
# transfer function: recover a known power law
# --------------------------------------------------------------------------
class TestTransferFit:
    def test_recovers_known_power_law(self):
        from app.harmonize.calibration import fit_transfer

        rng = np.random.default_rng(42)
        m = np.linspace(20, 4000, 48)
        true_slope, true_b = 1.35, 0.6
        v = np.expm1(true_b + true_slope * np.log1p(m)) * rng.lognormal(0, 0.12, m.size)
        fit = fit_transfer(m, v, "COUNTS")
        assert fit is not None
        assert fit.method == "theil_sen_log1p"
        assert fit.slope == pytest.approx(true_slope, abs=0.06)
        assert fit.intercept == pytest.approx(true_b, abs=0.08)
        assert fit.r2_in_sample > 0.97

    def test_predict_roundtrip(self):
        from app.harmonize.calibration import TransferFit

        fit = TransferFit("COUNTS", "theil_sen_log1p", 1.5, 0.7, 0.99, 0.98, 0.9, 10, 10)
        out = fit.predict(np.array([100.0]))
        assert out[0] == pytest.approx(np.expm1(0.7 + 1.5 * np.log1p(100.0)), rel=1e-6)

    def test_small_sample_fallback(self):
        from app.harmonize.calibration import fit_transfer

        fit = fit_transfer(np.array([10.0, 20.0, 30.0, 40.0]),
                           np.array([30.0, 60.0, 90.0, 120.0]), "COUNTS")
        assert fit is not None and fit.method == "ratio_fallback" and fit.slope == 1.0

    def test_fails_on_degenerate(self):
        from app.harmonize.calibration import fit_transfer

        assert fit_transfer(np.array([1.0, 2.0]), np.array([3.0, 4.0]), "COUNTS") is None


# --------------------------------------------------------------------------
# comparable-subset series (Phase 2)
# --------------------------------------------------------------------------
class TestSeamAndSeries:
    def test_seam_weight_ramp(self):
        from app.harmonize.series import seam_weight

        d = np.array([0, 50, 100, 125, 150, 200])
        w = seam_weight(d, 100, 150)
        assert w[0] == 0.0 and w[1] == 0.0
        assert w[2] == 0.0 and w[5] == 1.0
        assert w[4] == 1.0  # seam end
        assert abs(w[3] - 0.5) < 0.01  # cosine midpoint (era indicator only)


def _arrays(rows):
    """rows: (day_ord, satellite_code, daynight, static, frp)"""
    from app.harmonize.series import HotspotArrays

    n = len(rows)
    sensor = np.array([0 if r[1] in (0, 1) else 1 for r in rows], dtype=np.int8)
    return HotspotArrays(
        aoi_id="t",
        t_unix=np.array([r[0] * 86400.0 for r in rows]),
        date_ord=np.array([r[0] for r in rows], dtype=np.int32),
        lat=np.full(n, 30.0), lon=np.full(n, 31.0),
        frp_mw=np.array([r[4] for r in rows]),
        t4_k=np.full(n, 330.0), t11_k=np.full(n, 300.0),
        sensor=sensor,
        satellite=np.array([r[1] for r in rows], dtype=np.int8),
        conf_class=np.ones(n, dtype=np.int8),
        daynight=np.array([r[2] for r in rows], dtype=np.int8),
        area_km2=np.ones(n),
        static_source=np.array([bool(r[3]) for r in rows], dtype=bool),
        saturated=np.zeros(n, dtype=bool),
        band21_fallback=np.zeros(n, dtype=bool),
    )


class TestHarmonizeComparable:
    def _transfer(self):
        from app.harmonize.moy import fit_moy_transfer

        # Terra factor 2, Aqua factor 4, every month, three calibration years
        pooled, terra, aqua = {}, {}, {}
        for y in (2015, 2016, 2017):
            pooled[y] = {m: (3.0, 10.0) for m in range(1, 13)}
            terra[y] = {m: (2.0, 0.0) for m in range(1, 13)}
            aqua[y] = {m: (1.0, 0.0) for m in range(1, 13)}
        # SNPP counts: 2*Terra + 4*Aqua = 10 -> pooled ratio 10/3, terra 0/2
        for y in (2015, 2016, 2017):
            terra[y] = {m: (2.0, 4.0) for m in range(1, 13)}
            aqua[y] = {m: (1.0, 4.0) for m in range(1, 13)}
        return fit_moy_transfer(pooled, terra, aqua, min_month_total=0.0)

    def test_eras_provenance_and_reference_policy(self):
        from app.harmonize.series import harmonize_comparable

        # Terra (0) day non-static on days 10-11 (pre-VIIRS);
        # SNPP (2) day non-static on day 20; N20 (3) day non-static day 21;
        # SNPP night rows day 22; SNPP static day 23; MODIS night day 12.
        rows = [
            (10, 0, 0, 0, 5.0), (11, 1, 0, 0, 6.0),   # Terra/Aqua comp MODIS
            (12, 0, 1, 0, 5.0),                        # MODIS night: not comparable
            (20, 2, 0, 0, 9.0),                        # SNPP comp
            (21, 3, 0, 0, 7.0),                        # N20 comp (excluded pre-end)
            (22, 2, 1, 0, 3.0),                        # SNPP night layer
            (23, 2, 0, 1, 2.0),                        # SNPP static layer
        ]
        arr = _arrays(rows)
        t = self._transfer()
        ledger = harmonize_comparable(
            arr, 10, 23, t, None, viirs_start_ord=20,
            reference_code=2, successor_code=3, snpp_end_ord=10_000,
            cont=None,
        )
        prov = {int(d): p for d, p in zip(ledger["date_ord"], ledger["provenance"])}
        assert prov[10] == "MODIS_TRANSFERRED" and prov[12] == "MODIS_TRANSFERRED"
        assert prov[20] == "VIIRS_NATIVE"
        # day 12 MODIS night -> comparable MODIS = 0 -> n_h = 0 (factor*0)
        i12 = list(ledger["date_ord"]).index(12)
        assert ledger["n_harmonized"][i12] == 0.0
        # day 20: native SNPP comparable = 1
        i20 = list(ledger["date_ord"]).index(20)
        assert ledger["n_harmonized"][i20] == 1.0
        assert ledger["n_harmonized_lo"][i20] == ledger["n_harmonized"][i20]
        # day 21: N20 must NOT be summed into the native reference era
        i21 = list(ledger["date_ord"]).index(21)
        assert ledger["n_harmonized"][i21] == 0.0
        # layers: night on day 22, static on day 23 (VIIRS era only)
        i22, i23 = (list(ledger["date_ord"]).index(d) for d in (22, 23))
        assert ledger["n_night"][i22] == 1 and ledger["n_static"][i22] == 0
        assert ledger["n_static"][i23] == 1
        # static share on day 23: static 1 / (1 + 0 + 0)
        assert ledger["static_share"][i23] == 1.0
        # pre-VIIRS days have no layers (no MODIS equivalent)
        i10 = list(ledger["date_ord"]).index(10)
        assert ledger["n_static"][i10] == 0 and ledger["n_night"][i10] == 0

    def test_transferred_era_uses_factors_and_interval(self):
        from app.harmonize.series import harmonize_comparable

        rows = [(10, 0, 0, 0, 5.0), (11, 1, 0, 0, 6.0)]
        arr = _arrays(rows)
        t = self._transfer()
        ledger = harmonize_comparable(
            arr, 10, 11, t, None, viirs_start_ord=20,
            snpp_end_ord=10_000,
        )
        i10, i11 = (list(ledger["date_ord"]).index(d) for d in (10, 11))
        # day 10 is Terra-only (Aqua's first observation is day 11): the
        # Terra-specific factor applies (the pooled factor calibrates the
        # two-satellite mix). 1 comp Terra row x factor 2.
        assert ledger["n_harmonized"][i10] == pytest.approx(2.0, rel=1e-6)
        # day 11: pooled variant (LOYO tie -> pooled) x 1 comp MODIS row
        assert ledger["n_harmonized"][i11] == pytest.approx(10.0 / 3.0, rel=1e-6)
        lo, hi = t.pi  # degenerate (noiseless fixture): interval == value
        assert ledger["n_harmonized_lo"][i10] == pytest.approx(2.0 * (1 + lo), rel=1e-6)
        assert ledger["n_harmonized_hi"][i10] == pytest.approx(2.0 * (1 + hi), rel=1e-6)

    def test_continuity_direction_divide_not_multiply(self):
        from app.harmonize.series import harmonize_comparable

        # post-reference day: 4 N20 day rows, factor 2 -> S-NPP-equiv 2 (DIVIDE);
        # 3 N20 night rows / factor_night 1.5 -> night layer 2
        rows = ([(20, 2, 0, 0, 9.0)]
                + [(30, 3, 0, 0, 4.0)] * 4
                + [(31, 3, 1, 0, 3.0)] * 3)
        arr = _arrays(rows)
        t = self._transfer()
        cont = {"factor_day": 2.0, "factor_night": 1.5,
                "factor_lo": 1.8, "factor_hi": 2.2}
        ledger = harmonize_comparable(
            arr, 20, 31, t, None, viirs_start_ord=20,
            reference_code=2, successor_code=3, snpp_end_ord=25, cont=cont,
        )
        i30 = list(ledger["date_ord"]).index(30)
        i31 = list(ledger["date_ord"]).index(31)
        assert ledger["n_harmonized"][i30] == pytest.approx(4.0 / 2.0)
        # night layer: 3 / factor_night (flares are predominantly night rows)
        assert ledger["n_night"][i31] == pytest.approx(3.0 / 1.5)
        # interval from the factor spread: lo = val / f_hi, hi = val / f_lo
        assert ledger["n_harmonized_lo"][i30] == pytest.approx(4.0 / 2.2)
        assert ledger["n_harmonized_hi"][i30] == pytest.approx(4.0 / 1.8)

    def test_continuity_factors_from_arrays(self):
        from app.harmonize.series import continuity_factors

        # SNPP 100 day rows, N20 110 day rows in 2020 -> factor 1.10 (>1 =>
        # N20 higher, divide)
        rows = ([(d, 2, 0, 0, 1.0) for d in range(100, 200)]
                + [(d, 3, 0, 0, 1.0) for d in range(100, 210)])
        arr = _arrays(rows)
        cont = continuity_factors(arr, reference_code=2, successor_code=3)
        assert cont["factor_day"] == pytest.approx(110 / 100, rel=1e-6)
        assert cont["factor_day"] > 1.0
        assert cont["shared_months"] >= 1


# --------------------------------------------------------------------------
# climatology
# --------------------------------------------------------------------------
class TestClimatology:
    def test_loyo_flags_extreme_day_and_quiet_day(self):
        from app.harmonize.climatology import loyo_percentile_flags

        # 3 baseline years of a smooth seasonal cycle + one extreme spike
        days: list[int] = []
        vals: list[float] = []
        for y in (2012, 2013, 2014):
            for doy in range(1, 366):
                d = date(y, 1, 1).toordinal() + doy - 1
                days.append(d)
                vals.append(10.0 + 8.0 * np.sin(doy / 366 * 2 * np.pi))
        spike_ord = date(2015, 8, 1).toordinal()
        days.append(spike_ord)
        vals.append(300.0)
        quiet_ord = date(2015, 8, 2).toordinal()
        days.append(quiet_ord)
        vals.append(0.0)
        d = np.array(days, dtype=np.int64)
        v = np.array(vals)
        flags = loyo_percentile_flags(d, v, (2012, 2014), kernel_days=15,
                                      min_years=2)
        assert bool(flags["flag_high"][-2]) is True   # the 300 MW spike
        assert bool(flags["flag_low"][-1]) is True    # zero after active baseline
        # the spike day is above every baseline value of its ring
        assert flags["pct_n"][-2] == pytest.approx(100.0)

    def test_loyo_min_count_guard(self):
        from app.harmonize.climatology import loyo_percentile_flags

        # only ONE baseline year: the guard must suppress every flag
        days: list[int] = []
        vals: list[float] = []
        for doy in range(1, 366):
            d = date(2012, 1, 1).toordinal() + doy - 1
            days.append(d)
            vals.append(10.0)
        days.append(date(2015, 8, 1).toordinal())
        vals.append(500.0)  # would be an extreme spike without the guard
        flags = loyo_percentile_flags(np.array(days, dtype=np.int64),
                                      np.array(vals), (2012, 2012), min_years=5)
        assert not flags["flag_high"].any()
        assert not flags["flag_low"].any()
        assert np.isnan(flags["pct_n"]).all()

    def test_leap_day_slot_mapping(self):
        from app.harmonize.climatology import _doy_slot

        assert _doy_slot(date(2020, 2, 29)) == 60
        assert _doy_slot(date(2021, 3, 1)) == 61
        assert _doy_slot(date(2020, 3, 1)) == 61  # Mar 1 stays slot 61 in leap years


# --------------------------------------------------------------------------
# episodes
# --------------------------------------------------------------------------
def _arr_from(rows):
    from app.harmonize.series import HotspotArrays

    n = len(rows)
    return HotspotArrays(
        aoi_id="t",
        t_unix=np.array([r[0] * 86400.0 for r in rows]),
        date_ord=np.array([r[0] for r in rows], dtype=np.int32),
        lat=np.array([r[1] for r in rows]),
        lon=np.array([r[2] for r in rows]),
        frp_mw=np.array([r[3] for r in rows]),
        t4_k=np.full(n, 330.0), t11_k=np.full(n, 300.0),
        sensor=np.zeros(n, dtype=np.int8),
        satellite=np.ones(n, dtype=np.int8),
        conf_class=np.ones(n, dtype=np.int8),
        daynight=np.zeros(n, dtype=np.int8),
        area_km2=np.ones(n),
        static_source=np.zeros(n, dtype=bool),
        saturated=np.zeros(n, dtype=bool),
        band21_fallback=np.zeros(n, dtype=bool),
    )


class TestEpisodes:
    def test_chains_through_intermediates(self):
        from app.harmonize.episodes import cluster_episodes

        # day 1 A; day 2 B near A; day 3 C near B (far from A) -> one episode
        rows = [
            (739000, 30.000, 31.000, 10.0),
            (739001, 30.010, 31.010, 20.0),
            (739002, 30.020, 31.020, 30.0),
        ]
        eps = cluster_episodes(_arr_from(rows), space_km=5.0, time_days=2)
        assert len(eps) == 1
        assert eps[0].n_detections == 3
        assert eps[0].end_ord - eps[0].start_ord + 1 == 3
        assert eps[0].total_frp == pytest.approx(60.0)

    def test_separate_by_space_and_time(self):
        from app.harmonize.episodes import cluster_episodes

        rows = [
            (739000, 30.000, 31.000, 10.0),
            (739003, 30.000, 31.000, 10.0),   # 3 days later: too far in time
            (739001, 30.500, 31.500, 10.0),   # ~55 km away: too far in space
        ]
        eps = cluster_episodes(_arr_from(rows), space_km=5.0, time_days=2)
        assert len(eps) == 3

    def test_determinism_across_permutations(self):
        from app.harmonize.episodes import cluster_episodes

        rng = np.random.default_rng(7)
        rows = []
        for _ in range(120):
            d = int(rng.integers(739000, 739040))
            lat = 30.0 + rng.normal(0, 0.05)
            lon = 31.0 + rng.normal(0, 0.05)
            rows.append((d, lat, lon, float(rng.uniform(1, 50))))
        eps1 = cluster_episodes(_arr_from(rows), 5.0, 2)
        eps2 = cluster_episodes(_arr_from(list(reversed(rows))), 5.0, 2)
        k1 = [(e.start_ord, e.end_ord, e.n_detections, round(e.total_frp, 6)) for e in eps1]
        k2 = [(e.start_ord, e.end_ord, e.n_detections, round(e.total_frp, 6)) for e in eps2]
        assert k1 == k2


# --------------------------------------------------------------------------
# synthetic archive determinism
# --------------------------------------------------------------------------
class TestSynthDeterminism:
    def test_same_seed_same_archive(self):
        from app.seed.profiles import PROFILES_BY_ID
        from app.seed.synth import generate_aoi_records

        p = PROFILES_BY_ID["mediterranean-greece"]
        a = generate_aoi_records(p, p.aoi_id, date(2021, 1, 1), date(2021, 12, 31), 0.05, "TEST")
        b = generate_aoi_records(p, p.aoi_id, date(2021, 1, 1), date(2021, 12, 31), 0.05, "TEST")
        ka = [(r.acq_dt_utc, r.lat, r.lon, r.frp_mw, r.sensor_family) for r in a]
        kb = [(r.acq_dt_utc, r.lat, r.lon, r.frp_mw, r.sensor_family) for r in b]
        assert ka == kb and len(ka) > 100

    def test_viirs_modis_ratio_in_cropland(self):
        """The generator must reproduce the published detection-count bias:
        VIIRS 375 m reports several times more detections than MODIS over
        small-fire croplands (Vadrevu et al. 2018: 4.8x in Punjab)."""
        from app.seed.profiles import PROFILES_BY_ID
        from app.seed.synth import generate_aoi_records

        p = PROFILES_BY_ID["punjab-india"]
        recs = generate_aoi_records(p, p.aoi_id, date(2023, 1, 1), date(2023, 12, 31), 0.3, "TEST")
        nv = sum(1 for r in recs if r.sensor_family == "VIIRS")
        nm = sum(1 for r in recs if r.sensor_family == "MODIS")
        assert nm > 0
        assert nv / nm > 2.5

    def test_s_npp_record_ends_nov_2026(self):
        from app.seed.profiles import PROFILES_BY_ID
        from app.seed.synth import generate_aoi_records

        p = PROFILES_BY_ID["nile-delta-egypt"]
        recs = generate_aoi_records(p, p.aoi_id, date(2026, 9, 1), date(2026, 12, 31), 0.2, "TEST")
        snpp = [r for r in recs if r.satellite == "SNPP"]
        assert all(r.acq_dt_utc < datetime(2026, 11, 1) for r in snpp)


# --------------------------------------------------------------------------
# FIRMS parser
# --------------------------------------------------------------------------
class TestParser:
    CSV_MODIS = (
        "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,"
        "instrument,confidence,version,bright_t31,frp,daynight\n"
        "30.1,31.2,345.2,1.2,1.1,2023-10-15,1329,A,MODIS,85,6.1NRT,302.1,42.5,D\n"
        "30.2,31.3,9999.9,1.0,1.0,2023-10-15,1329,A,MODIS,l,6.1NRT,300.0,0.0,N\n"
        "99.0,31.3,300.0,1.0,1.0,2023-10-15,1329,A,MODIS,50,6.1NRT,300.0,1.0,D\n"
    )

    def test_parse_counts_and_rejects(self):
        from app.ingest.parser import parse_firms_csv

        recs, rej, total, samples = parse_firms_csv(self.CSV_MODIS, "t")
        assert total == 3 and rej == 1 and len(recs) == 2
        assert recs[0].confidence_class == "HIGH" and recs[0].confidence_raw == 85.0
        assert recs[0].band21_fallback is True  # brightness 345 K > 331 K
        assert recs[1].confidence_class == "LOW" and recs[1].confidence_raw is None
        assert recs[1].frp_mw == 0.0  # FIRMS zero-FRP rows are preserved

    def test_viirs_header_recognition(self):
        from app.ingest.parser import parse_firms_csv

        csv_text = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
            "instrument,confidence,version,bright_ti5,frp,daynight\n"
            "30.1,31.2,350.0,0.5,0.5,2023-10-15,0132,SNPP,VIIRS,h,2.0NRT,301.0,12.0,N\n"
        )
        recs, rej, total, _ = parse_firms_csv(csv_text, "t")
        assert total == 1 and rej == 0
        assert recs[0].satellite == "SNPP"
        assert recs[0].saturated is False

    def test_garbage_header_rejected(self):
        from app.ingest.parser import parse_firms_csv

        recs, rej, total, samples = parse_firms_csv("foo,bar\n1,2\n", "t")
        assert total == 0 and len(samples) == 1


# --------------------------------------------------------------------------
# API contract smoke (in-memory SQLite)
# --------------------------------------------------------------------------
class TestApiSmoke:
    @pytest.fixture(scope="class")
    def client(self):
        os.environ["EMBEREBEYE_DATABASE_URL"] = "sqlite:///./test_api.db"
        for m in list(sys.modules):
            if m.startswith("app"):
                del sys.modules[m]
        from fastapi.testclient import TestClient

        from app.db import create_all
        from app.main import app
        from app.seed.pipeline import run_seed

        create_all()
        os.environ["SEED_SCALE"] = "0.02"
        from app.config import get_settings

        get_settings.cache_clear()
        if os.path.exists("./test_api.db"):
            os.remove("./test_api.db")
        run_seed(force=True)
        with TestClient(app) as c:
            yield c

    def test_health_and_meta(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        m = client.get("/api/meta")
        assert m.status_code == 200
        assert len(m.json()["sensors"]) == 2

    def test_calendar_has_eras_and_anomalies(self, client):
        r = client.get("/api/calendar?aoi=punjab-india")
        assert r.status_code == 200
        body = r.json()
        provs = {c["provenance"] for c in body["cells"]}
        # Phase 2 eras: seed data is 2012+, i.e. entirely VIIRS-native
        assert "VIIRS_NATIVE" in provs
        assert body["summary"]["n_anomaly_high"] > 0

    def test_headline_endpoint(self, client):
        r = client.get("/api/analytics/headline?aoi=punjab-india")
        assert r.status_code == 200
        body = r.json()
        assert "current_total" in body and "verdict" in body
        # seed ends 2026-10-31, so the latest month is partial
        assert body["current_partial"] is True

    def test_export_csv_stream(self, client):
        r = client.get("/api/export?aoi=punjab-india&level=harmonized&format=csv")
        assert r.status_code == 200
        assert b"n_comparable_viirs" in r.content[:400]

    def test_live_fetch_reports_skipped_without_key(self, client):
        r = client.post("/api/live/fetch", json={"aoi_id": "punjab-india"})
        assert r.status_code == 200
        assert r.json()["status"] in {"SKIPPED", "UNREACHABLE", "FAILED"}

    def test_aoi_validation(self, client):
        bad = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0]]]}
        r = client.post("/api/aois", json={"name": "bad", "geojson": bad})
        assert r.status_code == 422
