#!/usr/bin/env python3
"""Seed all unseeded preset AOIs without touching real data."""
from __future__ import annotations

import sys
import logging

from app.db import SessionLocal
from app.seed.pipeline import seed_unseeded_presets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    force = "--force" in sys.argv
    db = SessionLocal()
    try:
        report = seed_unseeded_presets(db, force=force)
        print(f"Seed report: {report['status']} - {report['seeded_presets']} presets seeded.")
        for aoi_id, res in report.get("results", {}).items():
            print(f"  {aoi_id}: {res['status']}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
