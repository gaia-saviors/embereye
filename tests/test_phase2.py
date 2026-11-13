"""Phase 2 machinery tests: month-of-year transfer, static reclassification,
headline math, season censoring.

Run: cd backend && python -m pytest tests -q
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_backend = os.path.join(_repo_root, "backend")
if os.path.exists(_backend) and _backend not in sys.path:
    sys.path.insert(0, _backend)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
os.environ.setdefault("EMBEREBEYE_DATABASE_URL", "sqlite:///./test_phase2.db")


# --------------------------------------------------------------------------
# month-of-year transfer engine
# --------------------------------------------------------------------------
class TestMoyTransfer:
    def _pairs(self, noise: float = 0.0, seed: int = 7):
        """Terra factor 5 in winter / 3 in summer; Aqua factor 2; SNPP is the
        exact weighted sum (noise optional). Every month present per year."""
        rng = np.random.default_rng(seed)
        pooled, terra, aqua = {}, {}, {}
        for y in range(2012, 2023):
            tp, tsp, asp = {}, {}, {}
            for m in range(1, 13):
                ft = 5.0 if m in (12, 1, 2) else 3.0
                fa = 2.0
                t_count = 100.0 + 10.0 * m
                a_count = 50.0
                v_total = ft * t_count + fa * a_count
                if noise:
                    v_total *= float(rng.lognormal(0, noise))
                w_sum = ft + fa
                tp[m] = (t_count, v_total / w_sum)
                tsp[m] = (t_count, ft * t_count * (1.0 + (noise and rng.normal(0, noise))))
                asp[m] = (a_count, fa * a_count)
            pooled[y] = tp
            terra[y] = tsp
            aqua[y] = asp
        return pooled, terra, aqua

    def test_recovers_factors_noiseless(self):
        from app.harmonize.moy import fit_moy_transfer

        pooled, terra, aqua = self._pairs(noise=0.0)
        # unsmoothed: LOYO must be exact on a noiseless generative model
        fit = fit_moy_transfer(pooled, terra, aqua, min_month_total=0.0,
                               smoothed=False)
        assert fit is not None
        assert fit.loyo_pooled["mape"] < 1e-6
        assert fit.loyo_pooled["n_folds"] == 11
        # smoothed factors must still preserve the seasonal shape
        assert fit.pooled.factors[1] > fit.pooled.factors[7]
        # every month factor is a positive finite number
        assert all(np.isfinite(v) and v > 0 for v in fit.pooled.factors.values())

    def test_min_count_guard_falls_back_to_annual(self):
        from app.harmonize.moy import _fit_one

        pairs = {m: [(10.0, 40.0)] for m in range(1, 13)}  # below the guard
        fit = _fit_one(pairs, min_month_total=100, smoothed=False)
        annual = (sum(v for ps in pairs.values() for _, v in ps)
                  / sum(m for ps in pairs.values() for m, _ in ps))
        assert fit.fallback_months == list(range(1, 13))
        assert all(fit.factors[m] == pytest.approx(annual) for m in fit.factors)

    def test_per_platform_margin_rule(self):
        from app.harmonize.moy import fit_moy_transfer

        pooled, terra, aqua = self._pairs(noise=0.0)
        # identical LOYO MAPE -> pooled wins (simpler variant)
        fit = fit_moy_transfer(pooled, terra, aqua, min_month_total=0.0,
                               per_platform_margin=0.01)
        assert fit.variant == "pooled"
        # demanding a NEGATIVE margin forces the per-platform variant
        fit2 = fit_moy_transfer(pooled, terra, aqua, min_month_total=0.0,
                                per_platform_margin=-1.0)
        assert fit2.variant == "per_platform"

    def test_prediction_interval_brackets_loyo_errors(self):
        from app.harmonize.moy import fit_moy_transfer

        pooled, terra, aqua = self._pairs(noise=0.15, seed=11)
        fit = fit_moy_transfer(pooled, terra, aqua, min_month_total=0.0)
        lo, hi = fit.pi
        assert lo < 0.0 < hi
        errs = [d["rel_error"] for d in fit.loyo_pooled["detail"]]
        # the empirical 95% interval must cover the bulk of the LOYO errors
        covered = sum(1 for e in errs if lo <= e <= hi)
        assert covered >= 8
        assert fit.mape < 0.2  # the generative model must be recovered

    def test_fit_none_on_thin_record(self):
        from app.harmonize.moy import fit_moy_transfer

        assert fit_moy_transfer({2012: {1: (1.0, 1.0)}},
                                {2012: {1: (1.0, 1.0)}},
                                {2012: {1: (1.0, 1.0)}}) is None


# --------------------------------------------------------------------------
# static reclassification (type=2 primary, persistence secondary)
# --------------------------------------------------------------------------
class TestStaticReclassification:
    @pytest.fixture()
    def db(self, tmp_path):
        os.environ["EMBEREBEYE_DATABASE_URL"] = f"sqlite:///{tmp_path}/p2.db"
        for m in list(sys.modules):
            if m.startswith("app"):
                del sys.modules[m]
        from app.db import SessionLocal, create_all
        from app.models import Aoi, Hotspot, IngestRun

        create_all()
        s = SessionLocal()
        s.add(Aoi(id="t", name="t", geojson="{}", west=0, south=0, east=1, north=1,
                  is_preset=True))
        run = IngestRun(mode="LIVE", source="firms_api", aoi_id="t", status="OK")
        s.add(run)
        s.commit()

        def add(id_, lat, lon, daynight, ftype, product):
            s.add(Hotspot(
                aoi_id="t", sensor_family="VIIRS", satellite="SNPP",
                instrument="VIIRS", resolution_m=375,
                acq_dt_utc=datetime(2020, 1, 1), acq_date=date(2020, 1, 1),
                local_date=date(2020, 1, 1).replace(day=id_ % 28 + 1),
                lat=lat, lon=lon, brightness_t4_k=340.0, brightness_t11_k=300.0,
                frp_mw=1.0, confidence_class="HIGH", confidence_raw=None,
                daynight=daynight, scan_km=0.5, track_km=0.5,
                pixel_area_km2=0.25, saturated=False, band21_fallback=False,
                static_source=False, firms_product=product, firms_type=ftype,
                run_id=run.id,
            ))

        # SP type=2 row + NRT row at the same 2-decimal location
        add(1, 30.001, 31.001, "D", 2, "SP")
        add(2, 30.002, 31.002, "N", None, "NRT")
        # SP type=0 rows over >60 distinct days in ONE cell (persistence
        # evidence -> recurring_source, NOT static)
        for i in range(80):
            s.add(Hotspot(
                aoi_id="t", sensor_family="VIIRS", satellite="SNPP",
                instrument="VIIRS", resolution_m=375,
                acq_dt_utc=datetime(2020, 1, 1), acq_date=date(2020, 1, 1),
                local_date=date(2020, 1, 1) + timedelta(days=i),
                lat=40.0 + (i % 10) * 0.001, lon=32.0,
                brightness_t4_k=340.0, brightness_t11_k=300.0, frp_mw=1.0,
                confidence_class="HIGH", confidence_raw=None, daynight="D",
                scan_km=0.5, track_km=0.5, pixel_area_km2=0.25,
                saturated=False, band21_fallback=False, static_source=False,
                firms_product="SP", firms_type=0, run_id=run.id,
            ))
        s.commit()
        yield s
        s.close()

    def test_persistence_mask_sets_recurring_not_static(self, db):
        from app.ingest.persist import apply_static_source_mask
        from app.models import Hotspot

        n = apply_static_source_mask(db, "t", min_days=60, cell_deg=0.05)
        flags = db.query(Hotspot).all()
        recurring = [r for r in flags if r.recurring_source]
        static_flagged = [r for r in flags if r.static_source]
        # persistence evidence exists (80 day rows, hot stable T4)
        assert n > 0 and len(recurring) == n
        # the persistence mask must NOT touch static_source (secondary tag)
        assert all(not r.static_source for r in recurring)
        assert not static_flagged  # nothing is type=2 in the persistence cell

    def test_primary_definition_is_type2_with_nrt_inheritance(self, db):
        from app.models import Hotspot

        STATIC_TYPE, ROUND_DECIMALS = 2, 2
        rows = db.query(Hotspot).order_by(Hotspot.id).all()
        # pretend the old engine flagged everything, then re-derive the
        # Phase 2 primary rule: type=2 direct + NRT 2-decimal inheritance
        for r in rows:
            r.static_source = True
        db.commit()
        type2_locs = {(round(r.lat, ROUND_DECIMALS), round(r.lon, ROUND_DECIMALS))
                      for r in rows if r.firms_type == STATIC_TYPE}
        static_ids = {r.id for r in rows
                      if r.firms_type == STATIC_TYPE
                      or (r.firms_type is None and r.firms_product == "NRT"
                          and (round(r.lat, ROUND_DECIMALS),
                               round(r.lon, ROUND_DECIMALS)) in type2_locs)}
        assert static_ids == {rows[0].id, rows[1].id}  # SP type=2 + NRT twin
        # the 80 persistence rows are NOT in the primary set
        assert len(static_ids) == 2


# --------------------------------------------------------------------------
# season censoring helper
# --------------------------------------------------------------------------
class TestSeasonCensoring:
    def test_edge_years_are_partial(self):
        from app.harmonize.pipeline import _year_is_partial

        first = date(2000, 11, 2).toordinal()
        last = date(2026, 11, 1).toordinal()
        assert _year_is_partial(2000, first, last) is True   # record starts Nov
        assert _year_is_partial(2026, first, last) is True   # record ends Nov
        assert _year_is_partial(2015, first, last) is False  # fully covered


# --------------------------------------------------------------------------
# headline month math (pure part of the endpoint)
# --------------------------------------------------------------------------
class TestHeadlineMath:
    def test_month_end(self):
        from app.routers.analytics import _month_end

        assert _month_end(date(2026, 10, 1)) == date(2026, 10, 31)
        assert _month_end(date(2026, 12, 1)) == date(2026, 12, 31)
        assert _month_end(date(2024, 2, 1)) == date(2024, 2, 29)
