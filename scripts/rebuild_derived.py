"""Rebuild the derived tables (harmonized series, climatology, anomalies,
episodes, seasons) for every AOI from the stored hotspots.

Phase 2 upgrade path: after deploying v1.0.5 and running
`scripts/reclassify_static.py`, rebuild so the comparable-subset series,
layers, static share and LOYO anomalies are computed with the new engine:

    docker compose exec backend python scripts/rebuild_derived.py
    docker compose exec backend python scripts/rebuild_derived.py --aoi nile-delta-egypt

Idempotent per AOI (the pipeline wipes and replaces derived rows).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild derived tables per AOI")
    ap.add_argument("--aoi", metavar="AOI_ID", help="restrict to one AOI")
    ap.add_argument("--sqlite", metavar="PATH",
                    help="explicit local SQLite file (the only local-file opt-in)")
    args = ap.parse_args()

    import os

    if args.sqlite:
        os.environ["EMBEREBEYE_DATABASE_URL"] = f"sqlite:///{args.sqlite}"
    elif not (os.environ.get("EMBEREBEYE_DATABASE_URL") or os.environ.get("EMBEREBEYE_DB_URL")):
        print("ERROR: no database URL. Pass --sqlite PATH or run inside compose.",
              file=sys.stderr)
        return 2

    from app.config import get_settings
    from app.db import SessionLocal, create_all
    from app.models import Aoi
    from app.harmonize.pipeline import run_pipeline_for_aoi

    cfg = get_settings()
    print(f"target db: {cfg.database_url.split('@')[-1]}")
    create_all()
    with SessionLocal() as db:
        aoi_ids = ([args.aoi] if args.aoi else
                   list(db.execute(__import__("sqlalchemy")
                                   .select(Aoi.id).order_by(Aoi.id)).scalars()))
        for aoi_id in aoi_ids:
            aoi = db.get(Aoi, aoi_id)
            if aoi is None:
                print(f"{aoi_id}: not found, skipped")
                continue
            res = run_pipeline_for_aoi(db, aoi)
            print(f"{aoi_id}: {res.get('status')} "
                  f"agreement={res.get('agreement_stat')} "
                  f"episodes={res.get('episodes')}")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
