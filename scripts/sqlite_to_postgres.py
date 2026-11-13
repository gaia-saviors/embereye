#!/usr/bin/env python3
"""One-shot migration: SQLite embereye_dev.db -> Postgres (compose volume).

Copy path (option b of the v1.0.4 DB-plumbing task): every table of the
EmberEye schema is read from the source SQLite file and bulk-inserted into
the target database (default: the EMBEREBEYE_DATABASE_URL env var, i.e. the
same Postgres the compose stack uses). Idempotent by default for EMPTY
targets; --truncate-first clears the target tables first (in foreign-key
safe order) so a re-run converges.

The seeder will REFUSE to run against the migrated Postgres afterwards
(real-data guard) - that is the intended behaviour, see docs/DATA.md.

Usage (standalone PowerShell, repo root):
    $env:EMBEREBEYE_DATABASE_URL = "postgresql+psycopg://embereye:<pw>@localhost:5432/embereye"
    python scripts/sqlite_to_postgres.py --sqlite ./embereye_dev.db
    python scripts/sqlite_to_postgres.py --sqlite ./embereye_dev.db --truncate-first   # re-run
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Same .env courtesy as backfill_firms.py (no overrides of existing env).
from scripts._dotenv_shim import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT)

from sqlalchemy import func, insert, inspect, select, text  # noqa: E402

import app.models as models  # noqa: E402

# Insert parents before children.
TABLE_ORDER = [
    models.Aoi,
    models.IngestRun,
    models.Hotspot,
    models.CalibrationModel,
    models.DailyAggregate,
    models.HarmonizedDaily,
    models.FireEpisode,
    models.SeasonMetrics,
    models.AggregationJob,
    models.SystemState,
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SQLite -> Postgres migration")
    ap.add_argument("--sqlite", required=True,
                    help="path to the source SQLite file (e.g. .\\embereye_dev.db)")
    ap.add_argument("--truncate-first", action="store_true",
                    help="delete existing rows in the target tables first "
                         "(required when the target already has data)")
    args = ap.parse_args(argv)

    from app.db import database_url_from_env, describe_url

    src_path = Path(args.sqlite).expanduser()
    if not src_path.exists():
        print(f"ERROR: source not found: {src_path}", file=sys.stderr)
        return 2
    src_url = f"sqlite:///{src_path.resolve().as_posix()}"

    target_url = database_url_from_env()
    if not target_url or "sqlite" in target_url.split(":", 1)[0]:
        print(
            "ERROR: set EMBEREBEYE_DATABASE_URL to the Postgres target first, e.g.\n"
            "  $env:EMBEREBEYE_DATABASE_URL = \"postgresql+psycopg://embereye:<pw>@localhost:5432/embereye\"\n"
            "(this script never guesses, same rule as backfill_firms.py --sqlite).",
            file=sys.stderr)
        return 2

    from sqlalchemy import create_engine

    src = create_engine(src_url)
    dst = create_engine(target_url)
    print(f"source : {describe_url(src_url)}")
    print(f"target : {describe_url(target_url)}")

    try:
        with dst.connect():
            pass  # fail fast on a dead target before any copying
        with dst.connect() as dconn:
            tables = set(inspect(dconn).get_table_names())
        missing = [t for t in ("hotspots", "aois", "ingest_runs")
                   if t not in tables]
        if missing:
            # create_all on the target so an empty Postgres is bootstrapped.
            from app.db import Base, ensure_schema

            Base.metadata.create_all(dst)
            ensure_schema()
            print(f"target schema created (was missing: {', '.join(missing)})")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: target not reachable: {exc}", file=sys.stderr)
        return 2

    # ---- pre-flight: does the target already hold data? ---------------------
    counts_before = {}
    with dst.connect() as dconn:
        for model in TABLE_ORDER:
            counts_before[model.__tablename__] = int(
                dconn.execute(select(func.count()).select_from(model.__table__))
                .scalar() or 0)
    occupied = {t: n for t, n in counts_before.items() if n}
    if occupied and not args.truncate_first:
        print("\nTarget already holds rows:")
        for t, n in occupied.items():
            print(f"  {t:<20} {n:>9,}")
        print("Re-run with --truncate-first to clear the target tables first "
              "(FK-safe order), or back the target up (docs/BACKUP.md).")
        return 3

    grand = 0
    with src.connect() as sconn:
        with dst.begin() as txn:
            if args.truncate_first:
                for model in reversed(TABLE_ORDER):
                    txn.execute(text(f'DELETE FROM "{model.__tablename__}"'))
                print("target tables truncated (FK-safe order)")
            for model in TABLE_ORDER:
                table = model.__table__
                rows = [dict(r._mapping) for r in
                        sconn.execute(select(table)).mappings()]
                if rows:
                    for start in range(0, len(rows), 10000):
                        txn.execute(insert(table), rows[start:start + 10000])
                print(f"  {table.name:<20} {len(rows):>9,} rows copied")
                grand += len(rows)

    # ---- verification: source vs target counts ------------------------------
    bad = []
    with src.connect() as sconn, dst.connect() as dconn:
        for model in TABLE_ORDER:
            n_src = int(sconn.execute(
                select(func.count()).select_from(model.__table__)).scalar() or 0)
            n_dst = int(dconn.execute(
                select(func.count()).select_from(model.__table__)).scalar() or 0)
            if n_src != n_dst:
                bad.append((model.__tablename__, n_src, n_dst))
    if bad:
        print("\nVERIFY FAILED (do not trust the target):")
        for t, s, d in bad:
            print(f"  {t:<20} source={s:,} target={d:,}")
        return 4
    print(f"\nVERIFY OK - {grand:,} rows across {len(TABLE_ORDER)} tables, "
          "source and target counts match.")
    print("Next: docker compose up -d db backend frontend - the seeder will print")
    print("REAL DATA PRESENT - seeding refused and exit 0 (that is correct).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
