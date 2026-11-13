"""Shared minimal .env loader for the standalone (non-docker) scripts.

Reads REPO_ROOT/.env then REPO_ROOT/backend/.env into os.environ WITHOUT
overriding variables already set - so the FIRMS key and the DB URL resolve
regardless of the working directory. Mirrors the loader inside
scripts/backfill_firms.py.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(repo_root: Path) -> None:
    for name in (".env", "backend/.env"):
        path = repo_root / name
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
