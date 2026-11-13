"""Seeder container entry point: `python -m app.seed_run`."""
from __future__ import annotations

import os

from app.seed.pipeline import run_seed

if __name__ == "__main__":
    report = run_seed(force=os.environ.get("FORCE_SEED", "") == "1")
    # REFUSED (real FIRMS data present) is a deliberate no-op, not a failure:
    # the compose stack must come up healthy next to a real backfill.
    ok = report.get("status") in {"OK", "SKIPPED", "REFUSED"}
    raise SystemExit(0 if ok else 1)
