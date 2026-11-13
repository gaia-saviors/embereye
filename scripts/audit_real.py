#!/usr/bin/env python3
"""EmberEye real-data audit (read-only) - runs on Ali's machine.

Every subcommand loads the hotspots of one AOI from the database in use and
prints a markdown report (also written under data/audit_real/<stamp>/).
No writes to the database, ever.

Subcommands (Task 2 of the 2026-11-02 data-plumbing request):
  ratios       year + month-of-year detection ratio tables (Aqua<->VIIRS,
               Terra<->VIIRS, total and non-static) with the MODIS orbit-drift
               test: overpass hour + scan/track trend, day/night split.
  calibration  leave-one-year-out design inside 2012-2017 for MODIS->VIIRS
               calibration (Terra and Aqua separately), ratio-of-totals vs the
               current monthly power-law, error + prediction interval, and the
               proposed 2000-2011 factors with uncertainty.
  viirs        S-NPP vs NOAA-20 vs NOAA-21 agreement by month and by day
               (correlation, bias, day/night), reference-platform proposal and
               the S-NPP end-of-delivery continuity factor.
  static       static-mask criteria, confusion table vs FIRMS type=2 on SP
               rows, seasonal (crop-residue) misclassification estimate,
               non-static yearly counts.
  anomalies    the 2018 drop across satellites (real vs artifact), per-satellite
               day-coverage per year (fetch gaps), Aqua/S-NPP tails and the
               2026 SP/NRT seam view, state.json quiet-chunk/warning summary.
  all          everything above, one report directory.

Database: same fail-loudly rule as backfill_firms.py - EMBEREBEYE_DATABASE_URL
or --sqlite PATH (no silent fallback; the script prints what it opened).

Usage (standalone PowerShell, repo root):
    python scripts/audit_real.py all --sqlite ./embereye_dev.db
    python scripts/audit_real.py ratios --sqlite ./embereye_dev.db
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import fmean

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Reuse the backfill script's DB resolution (fail loudly, --sqlite, banner).
_SCRIPT = REPO_ROOT / "scripts" / "backfill_firms.py"
_spec = importlib.util.spec_from_file_location("backfill_firms", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
sys.modules["backfill_firms"] = bf
_spec.loader.exec_module(bf)

SAT_ORDER = ["Terra", "Aqua", "SNPP", "N20", "N21"]
VIIRS_SATS = ["SNPP", "N20", "N21"]
MONTHS = list(range(1, 13))
CURRENT_YEAR = date.today().year
COMPLETE_YEARS = [y for y in range(2000, CURRENT_YEAR)]  # full calendar years

State = dict  # rows: list[tuple], plus helper dicts


# ---------------------------------------------------------------------------
# Data loading (one AOI, one query, plain tuples)
# ---------------------------------------------------------------------------
def load_rows(db, aoi_id: str) -> list[tuple]:
    from sqlalchemy import select

    from app.models import Hotspot

    rows = db.execute(
        select(Hotspot.satellite, Hotspot.acq_date, Hotspot.local_date,
               Hotspot.lat, Hotspot.lon, Hotspot.frp_mw,
               Hotspot.brightness_t4_k, Hotspot.daynight, Hotspot.scan_km,
               Hotspot.track_km, Hotspot.static_source, Hotspot.firms_product,
               Hotspot.firms_type,
               Hotspot.acq_dt_utc,
               )
        .where(Hotspot.aoi_id == aoi_id)
    ).all()
    return [tuple(r) for r in rows]


# column indices into the row tuple
(S_SAT, S_ACQ, S_LOCAL, S_LAT, S_LON, S_FRP, S_T4, S_DN, S_SCAN, S_TRACK,
 S_STATIC, S_PROD, S_FTYPE, S_DT) = range(14)


def prep(rows: list[tuple]) -> dict:
    """Index rows by satellite and precompute year/month keys."""
    out: dict = {"rows": rows, "by_sat": defaultdict(list)}
    for r in rows:
        out["by_sat"][r[S_SAT]].append(r)
    return out


def year_of(r): return r[S_ACQ].year
def month_of(r): return r[S_ACQ].month


def count_matrix(data: dict, satellites: list[str], static_only: bool = False,
                 daynight: str | None = None) -> dict[int, dict[str, int]]:
    """satellite -> year -> row count (acq_date based)."""
    out: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sat in satellites:
        for r in data["by_sat"].get(sat, []):
            if static_only and r[S_STATIC]:
                continue
            if daynight and r[S_DN] != daynight:
                continue
            out[year_of(r)][sat] += 1
    return out


def ratio_table(data: dict, modis_sat: str, viirs_ref: str, static_only: bool,
                daynight: str | None, lines: list[str]) -> None:
    counts = count_matrix(data, [modis_sat, viirs_ref], static_only, daynight)
    label = f"{modis_sat}<->{viirs_ref}"
    tag = ("non-static" if static_only else "total") + \
          (f" [{daynight}]" if daynight else "")
    lines.append(f"\n#### {label} - {tag}")
    lines.append("| year | " + " | ".join([modis_sat, viirs_ref,
                                           f"ratio {viirs_ref}/{modis_sat}"]) + " |")
    lines.append("|---|---|---|---|")
    years = sorted(counts)
    for y in years:
        m, v = counts[y].get(modis_sat, 0), counts[y].get(viirs_ref, 0)
        ratio = f"{(v / m):.2f}" if m and v else "-"
        star = "" if y in COMPLETE_YEARS else " *(partial)*"
        lines.append(f"| {y}{star} | {m:,} | {v:,} | {ratio} |")
    # month-of-year (all years pooled)
    mo = {sat: defaultdict(int) for sat in (modis_sat, viirs_ref)}
    for sat in (modis_sat, viirs_ref):
        for r in data["by_sat"].get(sat, []):
            if static_only and r[S_STATIC]:
                continue
            if daynight and r[S_DN] != daynight:
                continue
            mo[sat][month_of(r)] += 1
    lines.append("\nMonth-of-year (all years pooled):")
    lines.append("| month | " + " | ".join([modis_sat, viirs_ref, "ratio"]) + " |")
    lines.append("|---|---|---|---|")
    for mth in MONTHS:
        m, v = mo[modis_sat][mth], mo[viirs_ref][mth]
        ratio = f"{(v / m):.2f}" if m and v else "-"
        lines.append(f"| {mth:02d} | {m:,} | {v:,} | {ratio} |")


def spearman(x, y):
    from scipy.stats import spearmanr

    if len(x) < 3:
        return float("nan")
    return float(spearmanr(x, y).statistic)


def cmd_ratios(data: dict, lines: list[str], out_dir: Path) -> None:
    lines.append("## 1. Detection ratio tables (Task 2.1)\n")
    lines.append("Reference platform: S-NPP (longest VIIRS record). "
                 "Ratios are VIIRS/MODIS; >1 means VIIRS sees more (expected: "
                 "375 m vs 1 km pixel, ~7x area). *(partial)* = current year.")
    for static_only in (False, True):
        lines.append(f"\n### {'Non-static' if static_only else 'Total (static included)'} detections")
        for modis_sat in ("Aqua", "Terra"):
            ratio_table(data, modis_sat, "SNPP", static_only, None, lines)
            lines.append("")
            ratio_table(data, modis_sat, "SNPP", static_only, "D", lines)
            lines.append("")
            ratio_table(data, modis_sat, "SNPP", static_only, "N", lines)

    # ---- orbit-drift test -------------------------------------------------
    lines.append("\n### Orbit-drift test: is the post-2018 ratio rise a MODIS artifact?\n")
    lines.append("If Terra/Aqua overpass times drift (or scan angle grows), the "
                 "1 km pixel covers hotter/fractional fires less consistently "
                 "and MODIS counts fall relative to VIIRS. Diagnostics per year: "
                 "mean overpass UTC hour of day detections, mean scan/track km, "
                 "day share. A steady drift + a falling count = artifact; a "
                 "stable overpass with a falling count = real sensitivity/land-use.")
    for sat in ("Terra", "Aqua"):
        rows = data["by_sat"].get(sat, [])
        if not rows:
            continue
        lines.append(f"\n#### {sat}")
        lines.append("| year | rows | day rows | day share | mean overpass UTC h (day) | mean scan km (day) | mean track km (day) |")
        lines.append("|---|---|---|---|---|---|---|")
        per_year = defaultdict(list)
        for r in rows:
            per_year[year_of(r)].append(r)
        stats: dict[int, tuple[float, float, float, float]] = {}
        for y in sorted(per_year):
            rr = per_year[y]
            day = [r for r in rr if r[S_DN] == "D"]
            if not day:
                continue
            hr = fmean(r[S_DT].hour + r[S_DT].minute / 60.0 for r in day)
            sc = fmean(float(r[S_SCAN]) for r in day)
            tr = fmean(float(r[S_TRACK]) for r in day)
            share = len(day) / len(rr)
            lines.append(f"| {y} | {len(rr):,} | {len(day):,} | {share:.2f} | {hr:.2f} | {sc:.2f} | {tr:.2f} |")
            if y in COMPLETE_YEARS:
                stats[y] = (hr, sc, tr, share)
        cnt = count_matrix(data, [sat])
        years = [y for y in sorted(stats) if cnt[y].get(sat)]
        if len(years) >= 5:
            hrs = [stats[y][0] for y in years]
            scs = [stats[y][1] for y in years]
            mods = [cnt[y][sat] for y in years]
            rho_h = spearman(years, hrs)
            rho_s = spearman(years, scs)
            rho_m = spearman(years, [np.log10(v) for v in mods])
            lines.append(f"\nSpearman(year, overpass hour) = {rho_h:.2f}; "
                         f"Spearman(year, scan km) = {rho_s:.2f}; "
                         f"Spearman(year, log10 count) = {rho_m:.2f}.")
            lines.append("Reading: |rho(year,hour)| or |rho(year,scan)| > ~0.7 "
                         "with a falling count indicates orbit-drift artifact; "
                         "near-zero drift with a falling count points to a real "
                         "change (or a sensitivity/budget change on the sensor).")

    lines.append("\n**Export:** the aggregated per-(year, month, satellite, "
                 "static, daynight) count matrix is written to `monthly_counts.csv` "
                 "next to this report.")
    # CSV: aggregated matrix (small enough to email)
    csv_path = out_dir / "monthly_counts.csv"
    agg: dict[tuple, int] = defaultdict(int)
    for sat in SAT_ORDER:
        for r in data["by_sat"].get(sat, []):
            agg[(sat, year_of(r), month_of(r), r[S_DN], int(bool(r[S_STATIC])))] += 1
    with csv_path.open("w", encoding="utf-8") as fh:
        fh.write("satellite,year,month,daynight,static,rows\n")
        for (sat, y, mth, dn, st), n in sorted(agg.items()):
            fh.write(f"{sat},{y},{mth},{dn},{st},{n}\n")
    lines.append(f"\n*(aggregated counts: {csv_path.name})*")


# ---------------------------------------------------------------------------
# Task 2.2 - calibration LOYO design
# ---------------------------------------------------------------------------
def _monthly_totals(rows, year, key_frp):
    m_count = m_frp = 0
    per_month_count = defaultdict(int)
    per_month_frp = defaultdict(float)
    for r in rows:
        if year_of(r) != year:
            continue
        per_month_count[month_of(r)] += 1
        if r[S_FRP] is not None:
            per_month_frp[month_of(r)] += float(r[S_FRP])
            m_frp += float(r[S_FRP])
        m_count += 1
    return per_month_count, per_month_frp, m_count, m_frp


def cmd_calibration(data: dict, lines: list[str], out_dir: Path) -> None:
    lines.append("## 2. Calibration design: MODIS -> VIIRS-equivalent (Task 2.2)\n")
    lines.append("Design under test: **calibrate on 2012-2017 only** (S-NPP-only "
                 "VIIRS era, stationary constellation), **apply to 2000-2011**, "
                 "native VIIRS from 2012. Terra and Aqua separately (different "
                 "overpass families). Validation: leave-one-year-out inside "
                 "2012-2017. Method A = annual ratio-of-totals; Method B = the "
                 "current engine's monthly Theil-Sen power law "
                 "(`app/harmonize/calibration.py:fit_transfer`).")
    from app.harmonize.calibration import fit_transfer

    csv_rows = []
    for modis_sat in ("Terra", "Aqua"):
        m_rows = data["by_sat"].get(modis_sat, [])
        v_rows = data["by_sat"].get("SNPP", [])
        years = list(range(2012, 2018))
        m_monthly: dict[int, dict] = {}
        v_monthly: dict[int, dict] = {}
        m_tot: dict[int, int] = {}
        v_tot: dict[int, int] = {}
        frp_tot: dict[int, tuple[float, float]] = {}
        for y in years:
            mc, mf, mc_t, mf_t = _monthly_totals(m_rows, y, "frp")
            vc, vf, vc_t, vf_t = _monthly_totals(v_rows, y, "frp")
            m_monthly[y], v_monthly[y] = (mc, mf), (vc, vf)
            m_tot[y], v_tot[y] = mc_t, vc_t
            frp_tot[y] = (mf_t, vf_t)
        for method_name, method_fn in (("A ratio-of-totals", "ratio"),
                                       ("B monthly power-law", "power")):
            preds: list[tuple[int, float, float]] = []  # (year, pred, actual)
            log_ratios = []
            for held in years:
                train = [y for y in years if y != held]
                if method_fn == "ratio":
                    tm = sum(m_tot[y] for y in train)
                    tv = sum(v_tot[y] for y in train)
                    f = (tv / tm) if tm else float("nan")
                    pred = f * m_tot[held]
                else:
                    months = sorted(set(m_monthly[held][0]) &
                                    set(v_monthly[held][0]))
                    tr_m = [m_monthly[y][0].get(m, 0) for y in train for m in months]
                    tr_v = [v_monthly[y][0].get(m, 0) for y in train for m in months]
                    fit = fit_transfer(np.asarray(tr_m, float),
                                       np.asarray(tr_v, float), "COUNTS")
                    if fit is None:
                        continue
                    pred = float(np.sum(fit.predict(
                        np.asarray([m_monthly[held][0].get(m, 0)
                                    for m in months], float))))
                actual = float(v_tot[held])
                if actual > 0 and pred > 0:
                    preds.append((held, pred, actual))
                    log_ratios.append(np.log(pred / actual))
            if not preds:
                continue
            errs = [p / a - 1.0 for _, p, a in preds]
            mape = fmean(abs(e) for e in errs) * 100
            bias = fmean(errs) * 100
            lr = np.asarray(log_ratios)
            pi_lo = float(np.exp(lr.mean() - 1.96 * lr.std()))
            pi_hi = float(np.exp(lr.mean() + 1.96 * lr.std()))
            tm = sum(m_tot[y] for y in years)
            tv = sum(v_tot[y] for y in years)
            f_all = tv / tm if tm else float("nan")
            lines.append(f"\n#### {modis_sat} - Method {method_name}")
            lines.append("| held-out year | predicted VIIRS | actual | rel. error |")
            lines.append("|---|---|---|---|")
            for held, pred, actual in preds:
                lines.append(f"| {held} | {pred:,.0f} | {actual:,.0f} | "
                             f"{(pred / actual - 1) * 100:+.1f}% |")
            lines.append(f"\nMAPE = {mape:.1f}%, mean bias = {bias:+.1f}%, "
                         f"95% prediction interval on the annual factor: "
                         f"[{pi_lo:.2f}x, {pi_hi:.2f}x]; full-window 2012-2017 "
                         f"factor = {f_all:.2f}.")
            csv_rows.append((modis_sat, method_name, mape, bias, f_all, pi_lo, pi_hi))
        tm_f = sum(frp_tot[y][0] for y in years)
        tv_f = sum(frp_tot[y][1] for y in years)
        if tm_f > 0:
            lines.append(f"\nFRP ratio (VIIRS/MODIS, 2012-2017 totals): "
                         f"{tv_f / tm_f:.2f} - compare with the counts factor "
                         f"above; the FRP transfer must be fitted separately "
                         f"(different units), the LOYO machinery is identical.")
    lines.append("\n### Recommendation (to carry into harmonized_daily)\n")
    lines.append("1. Adopt per-platform factors fitted on 2012-2017 totals, with "
                 "the LOYO 95% interval as the documented uncertainty.")
    lines.append("2. Apply the Terra factor to Terra-only days of 2000-11..2002-06, "
                 "the Aqua-inclusive blend (Terra+Aqua observed mix per day, "
                 "fallback 50/50) to 2002-07..2011-12.")
    lines.append("3. From 2012-01: native VIIRS (no scaling). The seam weight "
                 "already ramps 2012-06..2014-12; keep it but re-derive it AFTER "
                 "the factor change (Phase 2).")
    lines.append("4. Keep the current monthly power-law as a Methods-panel "
                 "comparison column; adopt whichever has the lower LOYO MAPE "
                 "per the tables above (fill the numbers from this run).")
    lines.append("\n*(summary: `calibration_loyo.csv`)*")
    csv_path = out_dir / "calibration_loyo.csv"
    with csv_path.open("w", encoding="utf-8") as fh:
        fh.write("satellite,method,mape_pct,bias_pct,factor_2012_2017,pi_lo,pi_hi\n")
        for sat, m, mape, bias, f, lo, hi in csv_rows:
            fh.write(f"{sat},{m},{mape:.2f},{bias:.2f},{f:.3f},{lo:.3f},{hi:.3f}\n")


# ---------------------------------------------------------------------------
# Task 2.3 - VIIRS constellation agreement
# ---------------------------------------------------------------------------
def cmd_viirs(data: dict, lines: list[str], out_dir: Path) -> None:
    lines.append("## 3. S-NPP vs NOAA-20 vs NOAA-21 (Task 2.3)\n")
    lines.append("The three VIIRS platforms observe the same fires (swaths "
                 "overlap, ~7% agreement in Ali's first look) - they must "
                 "NEVER be summed as independent counts. These tables quantify "
                 "pairwise agreement and produce the reference-platform "
                 "continuity factors.")
    monthly: dict[str, dict[tuple[int, int], int]] = {
        s: defaultdict(int) for s in VIIRS_SATS}
    daily_dn: dict[str, dict[str, dict[tuple[int, int], int]]] = {
        s: {"D": defaultdict(int), "N": defaultdict(int)} for s in VIIRS_SATS}
    for sat in VIIRS_SATS:
        for r in data["by_sat"].get(sat, []):
            key = (year_of(r), month_of(r))
            monthly[sat][key] += 1
            daily_dn[sat][r[S_DN]][key] += 1

    def pairwise(a: str, b: str, dn: str | None) -> None:
        keys = sorted(set(monthly[a]) & set(monthly[b]))
        if dn:
            ka = daily_dn[a][dn]
            kb = daily_dn[b][dn]
            keys = sorted(set(ka) & set(kb))
        if len(keys) < 6:
            return
        va = np.array([monthly[a][k] if not dn else daily_dn[a][dn][k] for k in keys], float)
        vb = np.array([monthly[b][k] if not dn else daily_dn[b][dn][k] for k in keys], float)
        from scipy.stats import pearsonr, spearmanr

        pr = float(pearsonr(va, vb).statistic)
        sr = float(spearmanr(va, vb).statistic)
        bias = fmean((vb - va)[va > 0] / va[va > 0]) * 100
        era = f"{keys[0][0]}-{keys[0][1]:02d} .. {keys[-1][0]}-{keys[-1][1]:02d}"
        tag = f" [{dn}]" if dn else ""
        lines.append(f"- **{b} vs {a}**{tag} over {era} ({len(keys)} months): "
                     f"Pearson r={pr:.3f}, Spearman rho={sr:.3f}, "
                     f"mean bias (B-A)/A = {bias:+.1f}%")

    for a, b in (("SNPP", "N20"), ("SNPP", "N21"), ("N20", "N21")):
        pairwise(a, b, None)
    lines.append("\nDay/night split (same pairs):")
    for a, b in (("SNPP", "N20"), ("SNPP", "N21")):
        pairwise(a, b, "D")
        pairwise(a, b, "N")

    lines.append("\n### Reference-platform rule (replaces summation)\n")
    lines.append("- **2012-01 .. 2018-03:** S-NPP alone (only platform flying).")
    lines.append("- **2018-04 .. S-NPP end (delivery ends 2026-11-02):** S-NPP is "
                 "the reference series; N20/N21 are used for gap-filling and "
                 "cross-checks, never added.")
    # continuity factor SNPP -> N20 over the overlap era
    keys = sorted(set(monthly["SNPP"]) & set(monthly["N20"]))
    if keys:
        s_tot = sum(monthly["SNPP"][k] for k in keys)
        n_tot = sum(monthly["N20"][k] for k in keys)
        f_snpp_n20 = n_tot / s_tot if s_tot else float("nan")
        lines.append(f"- **After S-NPP ends:** continue with NOAA-20 scaled by "
                     f"the overlap-era factor {f_snpp_n20:.3f} "
                     f"(= N20_total/SNPP_total over {len(keys)} shared months) "
                     f"so the series stays in S-NPP-equivalent units. N21 "
                     f"likewise (factor printed in the pairwise table above) "
                     f"when NOAA-20 eventually ends.")
        per_year_factors = []
        for y in sorted({k[0] for k in keys}):
            s_y = sum(monthly["SNPP"][(y, m)] for m in MONTHS)
            n_y = sum(monthly["N20"][(y, m)] for m in MONTHS)
            if s_y:
                per_year_factors.append(f"{y}: {n_y / s_y:.3f}")
        lines.append("- Uncertainty on the factor: recompute per year - "
                     + ", ".join(per_year_factors))
    lines.append("- Documented in the Methods panel as "
                 "`constellation policy: reference platform + continuity factor`.")
    lines.append("\n*(monthly matrix: `viirs_monthly.csv`)*")
    csv_path = out_dir / "viirs_monthly.csv"
    with csv_path.open("w", encoding="utf-8") as fh:
        fh.write("satellite,year,month,rows\n")
        for sat in VIIRS_SATS:
            for (y, m), n in sorted(monthly[sat].items()):
                fh.write(f"{sat},{y},{m},{n}\n")


# ---------------------------------------------------------------------------
# Task 2.4 - static mask audit
# ---------------------------------------------------------------------------
def cmd_static(data: dict, lines: list[str], out_dir: Path) -> None:
    lines.append("## 4. Static-source mask audit (Task 2.4)\n")
    lines.append("**The rule as implemented** (`docs/METHODOLOGY.md` section 9, "
                 "`app/ingest/persist.py:apply_static_source_mask`): a 0.05 deg "
                 "cell (~5.5 km) is flagged static when it has detections on "
                 ">= 60 distinct days AND its per-sensor daytime T4 series has "
                 "mean > 320 K and variance < 25 K^2. On top of that, SP rows "
                 "with FIRMS `type=2` are flagged directly and their rounded "
                 "(2-decimal, ~1.1 km) locations propagate the flag to NRT "
                 "rows. Flag only - never deleted - and industrial sources "
                 "**count as burning activity** per the challenge text: they "
                 "stay in totals, with a share display (Phase 2) and an opt-in "
                 "exclude toggle later.")

    # confusion table on SP rows with a type value
    conf: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sat in SAT_ORDER:
        for r in data["by_sat"].get(sat, []):
            if r[S_FTYPE] is None:
                continue
            tkey = f"type{r[S_FTYPE]}"
            conf[tkey]["static" if r[S_STATIC] else "not_static"] += 1
    if conf:
        lines.append("\n### Confusion table: FIRMS type (SP rows) vs static_source flag\n")
        lines.append("| FIRMS type | flagged static | not flagged | share flagged |")
        lines.append("|---|---|---|---|")
        for tkey in sorted(conf):
            s, n = conf[tkey]["static"], conf[tkey]["not_static"]
            lines.append(f"| {tkey} | {s:,} | {n:,} | {s / max(1, s + n) * 100:.1f}% |")
        tp = conf["type2"]["static"]
        fn = conf["type2"]["not_static"]
        fp = sum(conf[t]["static"] for t in conf if t != "type2")
        lines.append(f"\nDirect-rule view: type=2 rows flagged = {tp:,} "
                     f"(missed {fn:,}); flagged-but-not-type2 = {fp:,} "
                     f"(persistence-mask additions, most of them legitimate: "
                     f"the 60-day/T4 rule finds flares the FIRMS type column "
                     f"does not carry, and NRT rows have no type at all).")

    # crop-residue misclassification estimate
    cell_days: dict[tuple, set] = defaultdict(set)
    cell_rows: dict[tuple, int] = defaultdict(int)
    cell_months: dict[tuple, set] = defaultdict(set)
    cell_years: dict[tuple, set] = defaultdict(set)
    for sat in SAT_ORDER:
        for r in data["by_sat"].get(sat, []):
            if not r[S_STATIC]:
                continue
            cell = (round(float(r[S_LAT]), 2), round(float(r[S_LON]), 2))
            cell_days[cell].add(r[S_LOCAL])
            cell_rows[cell] += 1
            cell_months[cell].add(month_of(r))
            cell_years[cell].add(year_of(r))
    seasonal, flare_like = [], []
    for cell, days in cell_days.items():
        months = cell_months[cell]
        span = (max(days) - min(days)).days or 1
        # crop-like: detections concentrated in <= 5 distinct months-of-year
        if len(months) <= 5 and len(cell_years[cell]) >= 2:
            seasonal.append(cell)
        elif len(months) >= 9 or (len(months) / (span / 30.0) > 0.7):
            flare_like.append(cell)
    seasonal_rows = sum(cell_rows[c] for c in seasonal)
    total_flag_rows = sum(cell_rows.values())
    lines.append("\n### Recurring crop-residue misclassification estimate\n")
    lines.append(f"- flagged cells (2-decimal grid): {len(cell_days):,}, "
                 f"flagged rows: {total_flag_rows:,}")
    lines.append(f"- **crop-like** (flagged but detections concentrate in <= 5 "
                 f"months-of-year across >= 2 years = recurring seasonal "
                 f"burning, e.g. post-harvest residue): {len(seasonal):,} cells / "
                 f"{seasonal_rows:,} rows "
                 f"({seasonal_rows / max(1, total_flag_rows) * 100:.1f}% of flagged rows)")
    lines.append(f"- **flare-like** (active across >= 9 months-of-year or mostly "
                 f"continuous): {len(flare_like):,} cells")
    lines.append("- Interpretation: the crop-like bucket is where the 60-day "
                 "persistence rule and FIRMS type=2 most plausibly misclassify "
                 "recurring agriculture. Per the challenge text they REMAIN in "
                 "totals by default (they are burning activity); the Phase 2 "
                 "share display should surface this bucket explicitly.")

    lines.append("\n### Non-static yearly counts (static excluded)\n")
    lines.append("| year | " + " | ".join(SAT_ORDER) + " |")
    lines.append("|---|" + "---|" * len(SAT_ORDER))
    cnt_all = count_matrix(data, SAT_ORDER)
    cnt_ns = count_matrix(data, SAT_ORDER, static_only=True)
    for y in sorted(set(cnt_all)):
        star = "" if y in COMPLETE_YEARS else " *(partial)*"
        lines.append(f"| {y}{star} | "
                     + " | ".join(f"{cnt_ns[y].get(s, 0):,} / {cnt_all[y].get(s, 0):,}"
                                  for s in SAT_ORDER) + " |")
    lines.append("\nFormat: `non-static / total`. *(csv: `static_yearly.csv`)*")
    csv_path = out_dir / "static_yearly.csv"
    with csv_path.open("w", encoding="utf-8") as fh:
        fh.write("year,satellite,total,non_static,static_share_pct\n")
        for y in sorted(set(cnt_all)):
            for s in SAT_ORDER:
                t, ns = cnt_all[y].get(s, 0), cnt_ns[y].get(s, 0)
                fh.write(f"{y},{s},{t},{ns},{(t - ns) / t * 100 if t else 0:.2f}\n")

    # ------------------------------------------------------------------
    # Phase 2 item 4: the settling cross-tab + type=2-only sensitivity
    # ------------------------------------------------------------------
    lines.append("\n### Phase 2 cross-tab: persistence-only x crop-like x month-of-year\n")
    lines.append("Settles the open question: are the 19,645 persistence-only rows "
                 "legitimate flares (spread over the year) or recurring agriculture "
                 "(concentrated in the harvest window)? Buckets per flagged row: "
                 "`type2_direct` (FIRMS type=2), `type2_location` (type column absent, "
                 "2-decimal location matches a type=2 cell), `persistence_only` "
                 "(60-day/T4 rule only). The month-of-year histogram separates flares "
                 "(spread) from crop-residue burning (concentrated).")
    type2_locs: set[tuple[float, float]] = set()
    for sat in SAT_ORDER:
        for r in data["by_sat"].get(sat, []):
            if r[S_FTYPE] == 2:
                type2_locs.add((round(float(r[S_LAT]), 2), round(float(r[S_LON]), 2)))
    bucket_rows: dict[str, list[tuple]] = defaultdict(list)
    for sat in SAT_ORDER:
        for r in data["by_sat"].get(sat, []):
            if not r[S_STATIC]:
                continue
            loc = (round(float(r[S_LAT]), 2), round(float(r[S_LON]), 2))
            if r[S_FTYPE] == 2:
                bucket_rows["type2_direct"].append(r)
            elif loc in type2_locs:
                bucket_rows["type2_location"].append(r)
            else:
                bucket_rows["persistence_only"].append(r)
    lines.append("\n| bucket | rows |")
    lines.append("|---|---|")
    for b in ("type2_direct", "type2_location", "persistence_only"):
        lines.append(f"| {b} | {len(bucket_rows[b]):,} |")

    # crop-like classification of persistence-only cells (same rule as above)
    p_days: dict[tuple, set] = defaultdict(set)
    p_months: dict[tuple, set] = defaultdict(set)
    p_years: dict[tuple, set] = defaultdict(set)
    for r in bucket_rows["persistence_only"]:
        cell = (round(float(r[S_LAT]), 2), round(float(r[S_LON]), 2))
        p_days[cell].add(r[S_LOCAL])
        p_months[cell].add(month_of(r))
        p_years[cell].add(year_of(r))
    crop_cells = {c for c, days in p_days.items()
                  if len(p_months[c]) <= 5 and len(p_years[c]) >= 2}
    crop_months = [month_of(r) for r in bucket_rows["persistence_only"]
                   if (round(float(r[S_LAT]), 2), round(float(r[S_LON]), 2)) in crop_cells]
    other_months = [month_of(r) for r in bucket_rows["persistence_only"]
                    if (round(float(r[S_LAT]), 2), round(float(r[S_LON]), 2)) not in crop_cells]
    crop_rows = len(crop_months)
    p_total = len(bucket_rows["persistence_only"])
    lines.append(f"\nPersistence-only rows: {p_total:,}; **crop-like cells** "
                 f"({len(crop_cells):,} cells / {crop_rows:,} rows, "
                 f"{crop_rows / max(1, p_total) * 100:.1f}%).")
    lines.append("\n| bucket | " + " | ".join(f"{m:02d}" for m in MONTHS) + " |")
    lines.append("|---|" + "---|" * len(MONTHS))
    for name, months in (("crop-like", crop_months), ("other persistence", other_months)):
        hist = defaultdict(int)
        for m in months:
            hist[m] += 1
        lines.append(f"| {name} | " + " | ".join(f"{hist[m]:,}" for m in MONTHS) + " |")
    top = sorted(((m, crop_months.count(m)) for m in MONTHS),
                 key=lambda t: -t[1])[:3]
    share_top3 = sum(c for _, c in top) / max(1, crop_rows) * 100
    lines.append(f"\nReading: crop-like rows concentrate {share_top3:.0f}% of their "
                 f"mass in months {', '.join(f'{m:02d} ({c:,})' for m, c in top)}. "
                 "Concentration in the Oct-Nov post-harvest window supports the "
                 "recurring-agriculture reading; a flat histogram supports flares.")

    lines.append("\n### Sensitivity: day/non-static ratio with type=2-only as static\n")
    lines.append("Phase 2 makes FIRMS type=2 (+ NRT location inheritance) the PRIMARY "
                 "static definition; the persistence mask becomes a secondary "
                 "`recurring_source` tag. This table re-runs the comparable-subset "
                 "annual ratio under both definitions so the change is quantified "
                 "before the reclassify script runs.")

    def _is_static_def(r, definition: str) -> bool:
        if definition == "stored":
            return bool(r[S_STATIC])
        loc = (round(float(r[S_LAT]), 2), round(float(r[S_LON]), 2))
        return r[S_FTYPE] == 2 or loc in type2_locs

    lines.append("\n| year | ratio (stored flag) | ratio (type=2-only) |")
    lines.append("|---|---|---|")
    for y in sorted({year_of(r) for s in ("Terra", "Aqua", "SNPP")
                     for r in data["by_sat"].get(s, [])}):
        vals = {}
        for definition in ("stored", "type2_only"):
            mod = snp = 0
            for sat in ("Terra", "Aqua", "SNPP"):
                for r in data["by_sat"].get(sat, []):
                    if year_of(r) != y or r[S_DN] != "D" or _is_static_def(r, definition):
                        continue
                    if sat == "SNPP":
                        snp += 1
                    else:
                        mod += 1
            vals[definition] = (snp / mod) if mod and snp else None
        star = "" if y in COMPLETE_YEARS else " *(partial)*"
        a = f"{vals['stored']:.2f}" if vals["stored"] else "-"
        b = f"{vals['type2_only']:.2f}" if vals["type2_only"] else "-"
        lines.append(f"| {y}{star} | {a} | {b} |")
    lines.append("\nIf the two columns track each other within a few percent, the "
                 "type=2-only reclassification does not move the comparable series "
                 "materially; divergences mark years where persistence-only rows "
                 "were load-bearing (and the cross-tab above says why).")


# ---------------------------------------------------------------------------
# Task 2.5 - 2018 drop, coverage gaps, seam
# ---------------------------------------------------------------------------
def cmd_anomalies(data: dict, lines: list[str], out_dir: Path, state_path: Path) -> None:
    lines.append("## 5. Anomaly and coverage audit (Task 2.5)\n")

    lines.append("### 5a. The 2018 drop across satellites\n")
    counts = count_matrix(data, SAT_ORDER)
    counts_d = count_matrix(data, SAT_ORDER, daynight="D")
    counts_n = count_matrix(data, SAT_ORDER, daynight="N")
    lines.append("| year | " + " | ".join(SAT_ORDER) + " |")
    lines.append("|---|" + "---|" * len(SAT_ORDER))
    for y in (2016, 2017, 2018, 2019, 2020):
        lines.append(f"| {y} | " + " | ".join(f"{counts[y].get(s, 0):,}" for s in SAT_ORDER) + " |")
    lines.append("\nDay/night split for 2017 vs 2018 vs 2019:")
    lines.append("| satellite | 2017 D | 2018 D | 2019 D | 2017 N | 2018 N | 2019 N |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in SAT_ORDER:
        lines.append(f"| {s} | {counts_d[2017].get(s, 0):,} | {counts_d[2018].get(s, 0):,} "
                     f"| {counts_d[2019].get(s, 0):,} | {counts_n[2017].get(s, 0):,} "
                     f"| {counts_n[2018].get(s, 0):,} | {counts_n[2019].get(s, 0):,} |")
    drops = []
    for s in SAT_ORDER:
        a, b = counts[2017].get(s, 0), counts[2018].get(s, 0)
        if a:
            drops.append(f"{s} {(b - a) / a * 100:+.0f}%")
    lines.append("\n2018 vs 2017: " + ", ".join(drops) + ".")
    lines.append("Reading guide (fill verdict from the numbers): a drop that "
                 "appears in ALL satellites AND both day and night in the SAME "
                 "months is more likely real (drought/fuel/land-use); a drop "
                 "confined to MODIS day or to one platform points to an "
                 "instrumental artifact (orbit, budget, algorithm). Cross-check "
                 "the month table below against known 2018 Egyptian fire-season "
                 "reports before concluding.")

    lines.append("\n### 5b. Coverage per satellite-year (fetch-gap detector)\n")
    lines.append("Days with >= 1 detection per year. For an always-partly-burning "
                 "AOI a near-365 value means the archive walk is complete; big "
                 "holes = fetch gaps or true quiet years (compare with the "
                 "quiet_chunks count from the state file below).")
    lines.append("| year | " + " | ".join(SAT_ORDER) + " |")
    lines.append("|---|" + "---|" * len(SAT_ORDER))
    days_seen: dict[str, dict[int, set]] = {s: defaultdict(set) for s in SAT_ORDER}
    first_last: dict[str, dict[int, tuple]] = {s: {} for s in SAT_ORDER}
    for sat in SAT_ORDER:
        for r in data["by_sat"].get(sat, []):
            y = year_of(r)
            days_seen[sat][y].add(r[S_ACQ])
            fl = first_last[sat].get(y)
            first_last[sat][y] = ((min(fl[0], r[S_ACQ]), max(fl[1], r[S_ACQ]))
                                  if fl else (r[S_ACQ], r[S_ACQ]))
    for y in sorted({y for s in SAT_ORDER for y in days_seen[s]}):
        star = "" if y in COMPLETE_YEARS else " *(partial)*"
        lines.append(f"| {y}{star} | "
                     + " | ".join(f"{len(days_seen[s].get(y, ()))}" for s in SAT_ORDER)
                     + " |")

    lines.append("\n### 5c. Platform tails and the 2026 SP/NRT seam\n")
    last = {s: max((r[S_ACQ] for r in data["by_sat"].get(s, [])), default=None)
            for s in SAT_ORDER}
    lines.append("Last acq_date per satellite: "
                 + ", ".join(f"{s}={last[s]}" for s in SAT_ORDER if last[s]) + ".")
    lines.append("Aqua's tail (2026-10-28 in the first look) vs MODIS NRT "
                 "(Terra+Aqua combined product): if Terra reaches 2026-10-31 "
                 "but Aqua stops earlier, the gap is most plausibly real "
                 "(no afternoon detections) OR the MODIS NRT `satellite` split "
                 "changed - check the raw 2026-10 CSVs once raw saving is live.")
    lines.append("\n2026 monthly rows by product era (SP ends 2026-08-31 for "
                 "MODIS, 2026-07-31 for the VIIRS platforms):")
    lines.append("| month | " + " | ".join(f"{s} SP" for s in SAT_ORDER) + " | "
                 + " | ".join(f"{s} NRT" for s in SAT_ORDER) + " |")
    lines.append("|---|" + "---|" * (2 * len(SAT_ORDER)))
    seam: dict[tuple, int] = defaultdict(int)
    for sat in SAT_ORDER:
        for r in data["by_sat"].get(sat, []):
            if year_of(r) == 2026:
                seam[(month_of(r), sat, r[S_PROD])] += 1
    for mth in (7, 8, 9, 10, 11):
        if mth == 11 and not any(seam.get((11, s, p), 0) for s in SAT_ORDER for p in ("SP", "NRT")):
            continue
        row = [f"{seam.get((mth, s, 'SP'), 0):,}" for s in SAT_ORDER] + \
              [f"{seam.get((mth, s, 'NRT'), 0):,}" for s in SAT_ORDER]
        lines.append(f"| 2026-{mth:02d} | " + " | ".join(row) + " |")
    lines.append("\nSeam health check: SP counts must fall to ~0 the month "
                 "after each platform's SP max_date and NRT must take over; "
                 "any month with BOTH zero after the seam = fetch gap (re-run "
                 "the backfill for that window, it resumes from state).")

    lines.append("\n### 5d. state.json quiet-chunk / warning accounting\n")
    if state_path.exists():
        raw = json.loads(state_path.read_text())
        avail = raw.get("availability", {})
        ranges = avail.get("ranges", {})
        chunks = raw.get("chunks", {})
        lines.append(f"- state file: `{state_path}` (version "
                     f"{raw.get('state_version')}, script {raw.get('script_version')})")
        lines.append(f"- availability source: {avail.get('source')} "
                     f"({avail.get('fetched_at')})")
        lines.append("\n| product | chunks | OK | 0-row | first | last |")
        lines.append("|---|---|---|---|---|---|")
        for product, cmap in sorted(chunks.items()):
            ok = [e for e in cmap.values() if e.get("status") == "OK"]
            zero = sum(1 for e in ok if int(e.get("rows", 0)) == 0)
            if ok:
                lo = min(e["end"] for e in ok)
                hi = max(e["end"] for e in ok)
                lines.append(f"| {product} | {len(cmap)} | {len(ok)} | {zero} | {lo} | {hi} |")
            else:
                lines.append(f"| {product} | {len(cmap)} | 0 | 0 | - | - |")
        total_zero = sum(1 for cmap in chunks.values()
                         for e in cmap.values()
                         if e.get("status") == "OK" and int(e.get("rows", 0)) == 0)
        lines.append(f"\nTotal 0-row (quiet) chunks on disk: **{total_zero}** "
                     "(Ali's MODIS run reported 138 in one pass; this is the "
                     "cumulative file). A quiet chunk inside a product's "
                     "availability window is a legitimate no-fire window; only "
                     "0-row chunks OUTSIDE coverage would be data loss - the "
                     "v1.0.2+ loader invalidates those automatically.")
        lines.append("\nNOTE: the 468 per-run WARN lines are not persisted "
                     "per chunk; they were dominated by the truncation hint on "
                     "episodic windows (rows touching the window end). Re-run "
                     "with `--min-interval` raised if FIRMS throttling produced "
                     "retries; warnings never abort unless >50% of rows are "
                     "outside the requested window.")
    else:
        lines.append(f"- state file not found at {state_path} - run this on the "
                     "machine that did the backfill, or pass --state.")
    lines.append("\n*(coverage matrix: `coverage_days.csv`)*")
    csv_path = out_dir / "coverage_days.csv"
    with csv_path.open("w", encoding="utf-8") as fh:
        fh.write("satellite,year,days_with_data,first,last\n")
        for s in SAT_ORDER:
            for y in sorted(days_seen[s]):
                fl = first_last[s][y]
                fh.write(f"{s},{y},{len(days_seen[s][y])},{fl[0]},{fl[1]}\n")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="EmberEye real-data audit (read-only)")
    ap.add_argument("command", choices=["ratios", "calibration", "viirs",
                                        "static", "anomalies", "all"])
    ap.add_argument("--aoi", default="nile-delta-egypt")
    ap.add_argument("--sqlite", default=None,
                    help="explicit SQLite file (else EMBEREBEYE_DATABASE_URL)")
    ap.add_argument("--state",
                    default=str(REPO_ROOT / "data" / "firms_backfill" / "state.json"))
    ap.add_argument("--out", default=None,
                    help="output dir (default data/audit_real/<stamp>)")
    return ap


def main(argv: list[str]) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        bf.activate_db(args)
    except bf.BackfillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from app.db import SessionLocal, create_all

    create_all()
    db = SessionLocal()
    try:
        rows = load_rows(db, args.aoi)
    finally:
        db.close()
    print(f"Database: {os.environ.get('EMBEREBEYE_DATABASE_URL')}")
    print(f"AOI {args.aoi}: {len(rows):,} hotspot rows loaded "
          f"({', '.join(f'{s}={sum(1 for r in rows if r[S_SAT] == s):,}' for s in SAT_ORDER)})")
    if not rows:
        print("ERROR: no rows - wrong database? (see the Database: line above)",
              file=sys.stderr)
        return 3

    data = prep(rows)
    stamp = date.today().isoformat()
    out_dir = Path(args.out) if args.out else (
        REPO_ROOT / "data" / "audit_real" / stamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    sections: list[str] = [
        f"# EmberEye real-data audit - {args.command}",
        f"\nAOI: `{args.aoi}`  |  rows: {len(rows):,}  |  date: {stamp}",
        "\nGenerated by scripts/audit_real.py (read-only). Send the .md + .csv "
        "files back for the AUDIT_REAL.md update.",
    ]
    state_path = Path(args.state)
    todo = (["ratios", "calibration", "viirs", "static", "anomalies"]
            if args.command == "all" else [args.command])
    for name in todo:
        print(f"\n=== {name} ===", flush=True)
        if name == "ratios":
            cmd_ratios(data, sections, out_dir)
        elif name == "calibration":
            cmd_calibration(data, sections, out_dir)
        elif name == "viirs":
            cmd_viirs(data, sections, out_dir)
        elif name == "static":
            cmd_static(data, sections, out_dir)
        elif name == "anomalies":
            cmd_anomalies(data, sections, out_dir, state_path)

    report = out_dir / f"{args.command}.md"
    report.write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"\nReport written: {report}")
    print(f"Directory: {out_dir} (send back every file in it)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
