"""Recompute LOYO prediction error + 95% prediction interval at three
aggregation levels (annual / calendar-month / ISO-week) for the selected data.

This is the reproducible source for the metrics of the database snapshot
passed with --sqlite. Historical audit figures remain in docs/AUDIT_REAL.md
with their snapshot/date; do not assume they apply to a different database or
code revision. The report prints annual LOYO for BOTH platform variants
(pooled and per-platform), plus monthly and weekly LOYO at those aggregation
levels.

Inputs (exactly one):
  --monthly-csv PATH   the aggregate export (satellite,year,month,daynight,
                       static,rows). Annual + monthly LOYO; weekly needs
                       daily data, so it is reported as unavailable.
  --sqlite PATH        a full EmberEye SQLite DB (embereye_dev.db). Computes
                       annual + monthly + weekly from the comparable subset.

Optional:
  --aoi AOI_ID         AOI to use with --sqlite (default nile-delta-egypt)
  --json               also print the result as a single JSON object

The comparable subset is DAYTIME, NON-STATIC detections; MODIS = Terra+Aqua
(pooled) or per-platform; VIIRS = the reference platform (S-NPP). Calibration
window 2012-2022 (config.calibration_start / _end).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402


def _load_cfg():
    from app.config import get_settings

    return get_settings()


# ---------------------------------------------------------------------------
# input A: aggregate monthly CSV
# ---------------------------------------------------------------------------

def pairs_from_monthly_csv(path: str) -> dict[str, dict[int, dict[int, tuple[float, float]]]]:
    """{variant: {year: {month: (modis_comparable, snpp_comparable)}}}.

    comparable = daynight == D and static == 0 (the exact aggregate of the
    comparable-subset definition on stored rows)."""
    acc: dict[tuple, int] = defaultdict(int)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["daynight"] != "D" or r["static"] != "0":
                continue
            key = (r["satellite"], int(r["year"]), int(r["month"]))
            acc[key] += int(r["rows"])
    years = sorted({y for (_, y, _) in acc})
    out: dict[str, dict[int, dict[int, tuple[float, float]]]] = {
        "pooled": {}, "terra": {}, "aqua": {},
    }
    for y in years:
        pooled, terra, aqua = {}, {}, {}
        for m in range(1, 13):
            t = acc.get(("Terra", y, m), 0)
            a = acc.get(("Aqua", y, m), 0)
            s = acc.get(("SNPP", y, m), 0)
            pooled[m] = (float(t + a), float(s))
            terra[m] = (float(t), float(s))
            aqua[m] = (float(a), float(s))
        out["pooled"][y] = pooled
        out["terra"][y] = terra
        out["aqua"][y] = aqua
    return out


# ---------------------------------------------------------------------------
# input B: full SQLite DB
# ---------------------------------------------------------------------------

def pairs_from_db(sqlite_path: str, aoi_id: str):
    import os

    os.environ["EMBEREBEYE_DATABASE_URL"] = f"sqlite:///{sqlite_path}"
    for m in list(sys.modules):
        if m.startswith("app"):
            del sys.modules[m]
    from app.db import SessionLocal, create_all
    from app.harmonize.pipeline import (
        comparable_daily_pairs,
        comparable_monthly_pairs,
    )
    from app.harmonize.series import load_aoi_arrays

    create_all()
    with SessionLocal() as db:
        arr = load_aoi_arrays(db, aoi_id)
    cfg = _load_cfg()
    years = list(range(cfg.calibration_start, cfg.calibration_end + 1))
    monthly = comparable_monthly_pairs(arr, years)
    daily = comparable_daily_pairs(arr, years)
    return monthly, daily, years


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def _summarize(name: str, report: dict) -> dict:
    return {
        "variant": name,
        "mape": report["mape"],
        "bias": report["bias"],
        "pi95_low": report["pi_low"],
        "pi95_high": report["pi_high"],
        "n": report.get("n_folds") or report.get("n_errors"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--monthly-csv", metavar="PATH")
    src.add_argument("--sqlite", metavar="PATH")
    ap.add_argument("--aoi", default="nile-delta-egypt")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from app.harmonize.moy import fit_moy_transfer

    cfg = _load_cfg()
    cal_years = list(range(cfg.calibration_start, cfg.calibration_end + 1))

    daily = None
    if args.monthly_csv:
        pairs = pairs_from_monthly_csv(args.monthly_csv)
        pairs = {k: {y: v for y, v in d.items() if y in cal_years}
                 for k, d in pairs.items()}
        print(f"input: monthly aggregate CSV {args.monthly_csv}")
        print(f"calibration window: {cfg.calibration_start}-{cfg.calibration_end} "
              f"({len([y for y in cal_years if y in pairs['pooled']])} full years present)")
    else:
        pairs, daily, _ = pairs_from_db(args.sqlite, args.aoi)
        print(f"input: SQLite DB {args.sqlite} (AOI {args.aoi})")
        print(f"calibration window: {cfg.calibration_start}-{cfg.calibration_end}")

    pooled_years = {y for y in cal_years if y in pairs["pooled"]}
    if len(pooled_years) < 3:
        print(f"ERROR: only {len(pooled_years)} calibration years with data - "
              "LOYO needs >= 3.", file=sys.stderr)
        return 2

    transfer = fit_moy_transfer(
        {y: pairs["pooled"][y] for y in pooled_years},
        {y: pairs["terra"][y] for y in pooled_years},
        {y: pairs["aqua"][y] for y in pooled_years},
        daily_by_year=daily,
    )
    if transfer is None:
        print("ERROR: fit returned None (thin record).", file=sys.stderr)
        return 2

    rows = [
        ("annual (pooled Terra+Aqua)", transfer.loyo_pooled),
        ("annual (per-platform Terra/Aqua)", transfer.loyo_per_platform),
        ("calendar-month (chosen variant layout)", transfer.loyo_monthly),
    ]
    out: dict[str, dict] = {}
    print("\n== LOYO prediction error and empirical 95% PI (comparable subset,"
          " calibration 2012-2022) ==")
    print(f"{'aggregation / variant':44s} {'MAPE':>7s} {'bias':>8s} "
          f"{'PI95 low':>9s} {'PI95 high':>10s} {'n':>5s}")
    for label, rep in rows:
        s = _summarize(label, rep)
        out[label] = s
        print(f"{label:44s} {s['mape']*100:6.2f}% {s['bias']*100:7.2f}% "
              f"{s['pi95_low']*100:8.2f}% {s['pi95_high']*100:9.2f}% {s['n']:5d}")
    if daily:
        s = _summarize("ISO-week (chosen variant layout)", transfer.loyo_weekly)
        out["ISO-week (chosen variant layout)"] = s
        print(f"{'ISO-week (chosen variant layout)':44s} {s['mape']*100:6.2f}% "
              f"{s['bias']*100:7.2f}% {s['pi95_low']*100:8.2f}% "
              f"{s['pi95_high']*100:9.2f}% {s['n']:5d}")
    else:
        out["ISO-week"] = {"note": "weekly LOYO needs the full DB (daily counts); "
                                   "run with --sqlite embereye_dev.db"}
        print(f"{'ISO-week':44s} -  (needs --sqlite; not computable from a "
              "monthly aggregate)")

    print("\n== reconciliation notes ==")
    a, b = transfer.loyo_pooled, transfer.loyo_per_platform
    print(f"* chosen variant: {transfer.variant} (per-platform adopted only if its "
          f"annual MAPE is lower by > {transfer.per_platform_margin:.2f}); "
          f"pooled MAPE {a['mape']*100:.2f}% vs per-platform {b['mape']*100:.2f}%.")
    print(f"* the 'two different annual PIs' in the handoff are these two "
          f"variant PIs: pooled ({a['pi_low']*100:.1f}/+{a['pi_high']*100:.1f}) vs "
          f"per-platform ({b['pi_low']*100:.1f}/+{b['pi_high']*100:.1f}). ONE number "
          f"is carried into the series/UI: the CHOSEN variant "
          f"({transfer.pi[0]*100:.1f}/+{transfer.pi[1]*100:.1f}).")
    mlo, mhi = transfer.pi_monthly
    print(f"* month view band uses the MONTHLY LOYO PI "
          f"({mlo*100:.1f}/+{mhi*100:.1f});", end=" ")
    if transfer.loyo_weekly.get("n_errors"):
        wlo, whi = transfer.pi_weekly
        print(f"week view the WEEKLY LOYO PI ({wlo*100:.1f}/+{whi*100:.1f}) - "
              "never the annual PI rescaled.")
    else:
        print("week view needs the full DB (run with --sqlite) - never the "
              "annual PI rescaled.")

    if args.json:
        print("\n" + json.dumps({
            "calibration_years": sorted(pooled_years),
            "variant_chosen": transfer.variant,
            "results": out,
        }, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
