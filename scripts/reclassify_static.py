"""Phase 2 static reclassification for an EXISTING database (one-shot).

Re-derives the static classification under the approved Phase 2 policy:
  PRIMARY   static_source := FIRMS type=2 on SP rows; NRT rows inherit via
            the 2-decimal location match to SP type=2 cells (the same
            propagation the backfill uses at ingest time).
  SECONDARY recurring_source := the 60-day / T4-stability persistence mask
            (app/ingest/persist.py:apply_static_source_mask). No longer
            overwrites static_source.

Run AFTER deploying v1.0.5 (create_all adds the recurring_source column),
then rebuild the derived tables (pipeline re-run) so the comparable subset,
static share and layers pick up the new flags:

    docker compose exec backend python scripts/reclassify_static.py
    # then re-run the aggregation for each AOI (UI button or):
    docker compose exec backend python -c "..."   # see printout at the end

Read-only about detections themselves; only the two flag columns change.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STATIC_TYPE = 2
ROUND_DECIMALS = 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 2 static reclassification")
    ap.add_argument("--sqlite", metavar="PATH",
                    help="explicit local SQLite file (the only local-file opt-in)")
    ap.add_argument("--aoi", metavar="AOI_ID", help="restrict to one AOI")
    args = ap.parse_args()

    if args.sqlite:
        import os
        os.environ["EMBEREBEYE_DATABASE_URL"] = f"sqlite:///{args.sqlite}"
    else:
        url = None
        for var in ("EMBEREBEYE_DATABASE_URL", "EMBEREBEYE_DB_URL"):
            if os_env(var):
                url = "set"
                break
        if url is None:
            print("ERROR: no database URL. Pass --sqlite PATH or export "
                  "EMBEREBEYE_DATABASE_URL (compose already sets it inside "
                  "the container).", file=sys.stderr)
            return 2

    from app.config import get_settings
    from app.db import SessionLocal, create_all
    from app.ingest.persist import apply_static_source_mask
    from app.models import Aoi, Hotspot

    cfg = get_settings()
    print(f"target db: {cfg.database_url.split('@')[-1]}")
    create_all()  # idempotent; adds recurring_source + Phase 2 columns

    from sqlalchemy import select, update

    with SessionLocal() as db:
        aoi_ids = [args.aoi] if args.aoi else [
            r for r in db.execute(select(Aoi.id).order_by(Aoi.id)).scalars()
        ]
        for aoi_id in aoi_ids:
            rows = db.execute(
                select(Hotspot.id, Hotspot.firms_type, Hotspot.firms_product,
                       Hotspot.lat, Hotspot.lon, Hotspot.static_source)
                .where(Hotspot.aoi_id == aoi_id)
            ).all()
            if not rows:
                continue
            n_real = sum(1 for r in rows if r[2])
            if n_real == 0:
                print(f"{aoi_id}: seed-only AOI, skipped (seed flags untouched)")
                continue

            # 1) PRIMARY: type=2 direct on SP rows + 2-decimal locations
            type2_locs = {(round(r[3], ROUND_DECIMALS), round(r[4], ROUND_DECIMALS))
                          for r in rows if r[1] == STATIC_TYPE}
            direct_ids = [r[0] for r in rows if r[1] == STATIC_TYPE]
            inherit_ids = [r[0] for r in rows
                           if r[1] is None and r[2] == "NRT"
                           and (round(r[3], ROUND_DECIMALS),
                                round(r[4], ROUND_DECIMALS)) in type2_locs]
            static_ids = list(set(direct_ids) | set(inherit_ids))

            # 2) SECONDARY: persistence mask -> recurring_source (reset first)
            db.execute(update(Hotspot).where(Hotspot.aoi_id == aoi_id)
                       .values(recurring_source=False))
            db.commit()
            recurring_n = apply_static_source_mask(
                db, aoi_id, min_days=60, cell_deg=0.05)

            # 3) write the primary definition (was: type2 + persistence mixed)
            db.execute(update(Hotspot).where(Hotspot.aoi_id == aoi_id)
                       .values(static_source=False))
            db.commit()
            for start in range(0, len(static_ids), 10000):
                db.execute(update(Hotspot)
                           .where(Hotspot.id.in_(static_ids[start:start + 10000]))
                           .values(static_source=True))
                db.commit()

            old_n = sum(1 for r in rows if r[5])
            print(f"{aoi_id}: static_source {old_n:,} -> {len(static_ids):,} "
                  f"(type2_direct={len(direct_ids):,}, "
                  f"nrt_inherited={len(inherit_ids):,}); "
                  f"recurring_source={recurring_n:,}")
        print("\nNext: rebuild the derived tables so the harmonized series, "
              "static share and layers use the new flags (UI 'Rebuild' or "
              "POST /api/aois/{id}/aggregate).")
    return 0


def os_env(var: str) -> str | None:
    import os
    return os.environ.get(var)


if __name__ == "__main__":
    raise SystemExit(main())
