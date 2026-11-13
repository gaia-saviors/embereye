#!/usr/bin/env python3
"""EmberEye FIRMS historical backfill (resumable, rate-limit aware).

Rebuilds the real MODIS/VIIRS record for one AOI by walking every FIRMS
product across its ACTUAL availability window:

  per sensor:  SP from window start .. SP max_date
               NRT from the day after SP max_date .. end

SP availability never covers through today; the seam day (first NRT day) is
derived from the SP max_date, never assumed. Availability comes from
`/api/data_availability/csv/{MAP_KEY}/ALL` (verified working 2026-11-01);
if that endpoint fails, the hardcoded 2026-11-01 table below is used and the
run is labelled FALLBACK.

FIRMS area API contract (official docs, checked 2026-11-01):
  * DAY_RANGE is 1..5 days - five is the hard cap; larger values are not
    defined by the API and are rejected by the script.
  * /api/area/csv/KEY/SOURCE/BBOX/DAY_RANGE/DATE returns
    [DATE .. DATE + DAY_RANGE - 1] - FORWARD from DATE. Chunks are anchored
    at their START day; anchoring at the end day would silently fetch the
    days after the chunk instead.
  * `--verify-chunking` proves both properties on live data before a long
    run: one N-day request must reproduce the N x 1-day union exactly, and
    the direction probe reports FORWARD vs BACKWARD.

Key invariants (script v2.2 / state format v2, 2026-11-02)
  * A header-only CSV inside a product's availability window is a QUIET
    window (0 rows, legitimate). A window outside the product's coverage is
    OUT_OF_COVERAGE - never recorded as quiet.
  * Chunk state recorded beyond the SP max_date (the old script's bug: SP
    requested past its archive, header-only, stored as 0-row "quiet") is
    invalidated on load and never trusted. `--invalidate-only` shows the
    damage report before any fetch.
  * NRT rows have no `type` column: firms_type is NULL there. Static-source
    masking still works: a location list is built from SP rows with type=2
    (rounded coordinates) and applied to NRT rows - including a final DB
    pass that catches rows ingested before their sensor's SP window (MODIS
    NRT is deliberately fetched before the 26-year MODIS SP walk).
  * Any response that is not a FIRMS CSV (e.g. "Invalid MAP_KEY", rate-limit
    text, HTML) aborts the run with a non-zero exit code and prints the body.
  * Every request has a hard timeout, bounded retries, Retry-After support
    and a minimum interval; one hanging request cannot freeze the run.
  * Per-chunk sanity: rows outside the requested day window are counted and
    abort past 50%; a short day-span with rows near the window end warns
    about a possibly truncated window start.
  * Every fetched chunk's raw response body is saved verbatim under
    data/firms_backfill/raw/<product>/<start>_<end>.csv BEFORE parsing and
    survives --fresh (only chunks fetched after v1.0.4 have raw copies).

Database (v1.0.4, the silent-SQLite lesson):
  * The scripts read EMBEREBEYE_DATABASE_URL (or EMBEREBEYE_DB_URL).
  * With no env var set, this script FAILS LOUDLY (exit 2) instead of
    silently creating ./embereye_dev.db. Explicit opt-in: --sqlite PATH.
  * The resolved target (engine + host/path, never a password) is printed
    at startup, and --status reports what is actually in that database.

Fetch order (most time-critical first; --sensors controls the split):
    SNPP (SP -> NRT), NOAA20 (SP -> NRT), NOAA21 (SP -> NRT),
    MODIS (NRT first, then SP newest -> oldest).

Usage (standalone PowerShell on the host - reads repo .env, DB via env):
    cd embereye
    $env:EMBEREBEYE_DATABASE_URL = "postgresql+psycopg://embereye:<pw>@localhost:5432/embereye"
    python scripts/backfill_firms.py --dry-run
    python scripts/backfill_firms.py --status
    python scripts/backfill_firms.py --verify-chunking
    python scripts/backfill_firms.py --fresh
Usage (inside the backend container):
    docker compose exec backend python scripts/backfill_firms.py --dry-run
    docker compose exec backend python scripts/backfill_firms.py --invalidate-only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_dotenv() -> None:
    """Minimal .env loader for standalone (non-docker) runs.

    Reads REPO_ROOT/.env then REPO_ROOT/backend/.env into os.environ WITHOUT
    overriding variables already set, before any app.* import - so the FIRMS
    key and the DB URL resolve regardless of the working directory.
    """
    for name in (".env", "backend/.env"):
        path = REPO_ROOT / name
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


_load_dotenv()

import httpx  # noqa: E402  (backend dependency)

from app.config import get_settings  # noqa: E402

SCRIPT_VERSION = "2.2"          # DB fail-loudly + --status/--build-snapshot + scoped --fresh (2026-11-02)
EMBEREBYE_VERSION = "1.0.4"
USER_AGENT = f"EmberEye/{EMBEREBYE_VERSION} (NASA Space Apps 2026 backfill)"

# ---------------------------------------------------------------------------
# Product catalog + hardcoded availability (verified against
# data_availability on 2026-11-01). Fallback when the live availability CSV
# cannot be fetched/parsed. Never assume SP covers today.
# ---------------------------------------------------------------------------
SENSORS: dict[str, tuple[str, str]] = {
    # sensor  -> (SP product, NRT product)
    "MODIS": ("MODIS_SP", "MODIS_NRT"),
    "SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "NOAA20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
    "NOAA21": ("VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT"),
}
PRODUCT_FAMILY = {
    "MODIS_SP": "MODIS", "MODIS_NRT": "MODIS",
    "VIIRS_SNPP_SP": "VIIRS", "VIIRS_SNPP_NRT": "VIIRS",
    "VIIRS_NOAA20_SP": "VIIRS", "VIIRS_NOAA20_NRT": "VIIRS",
    "VIIRS_NOAA21_SP": "VIIRS", "VIIRS_NOAA21_NRT": "VIIRS",
}
SENSOR_SATELLITES = {
    "MODIS": "Terra + Aqua", "SNPP": "SNPP", "NOAA20": "N20", "NOAA21": "N21",
}
# sensor -> hotspots.satellite values it owns (scoped --fresh deletes).
# MODIS is the Terra + Aqua pair; the FIRMS satellite column splits them.
SENSOR_SAT_SET: dict[str, set[str]] = {
    "MODIS": {"Terra", "Aqua"},
    "SNPP": {"SNPP"},
    "NOAA20": {"N20"},
    "NOAA21": {"N21"},
}
FALLBACK_AS_OF = date(2026, 11, 1)
# Official challenge scope: the calendar covers early 2000s to present.
# 2000-11-01 is MODIS_SP min_date (Terra); Aqua rows appear from 2002-07 and
# stay split via the `satellite` column. Later sensors clamp to their own
# availability (SNPP 2012-01-20, NOAA20 2018-04-01, NOAA21 2023-03-01).
DEFAULT_START = date(2000, 11, 1)
# Official FIRMS area API cap (docs checked 2026-11-01): DAY_RANGE is 1..5.
MAX_CHUNK_DAYS = 5
# Most time-critical first (Ali's run plan): S-NPP delivery ends first.
DEFAULT_SENSOR_ORDER = ["SNPP", "NOAA20", "NOAA21", "MODIS"]


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


# product -> (min_date, max_date) as reported by FIRMS on 2026-11-01
FALLBACK_AVAILABILITY: dict[str, tuple[date, date]] = {
    "MODIS_SP": (_d("2000-11-01"), _d("2026-08-31")),
    "MODIS_NRT": (_d("2026-09-01"), _d("2026-11-01")),
    "VIIRS_SNPP_SP": (_d("2012-01-20"), _d("2026-07-31")),
    "VIIRS_SNPP_NRT": (_d("2026-08-01"), _d("2026-11-01")),
    "VIIRS_NOAA20_SP": (_d("2018-04-01"), _d("2026-07-31")),
    "VIIRS_NOAA20_NRT": (_d("2026-08-01"), _d("2026-11-01")),
    "VIIRS_NOAA21_SP": (_d("2023-03-01"), _d("2026-07-31")),
    # NRT archive reaches further back than SP end; the SP/NRT seam still
    # uses SP max_date + 1 (SP wins inside the overlap, better quality).
    "VIIRS_NOAA21_NRT": (_d("2024-01-17"), _d("2026-11-01")),
}

STATIC_SOURCE_TYPE = 2          # FIRMS type 2 = other static land source
STATIC_ROUND_DECIMALS = 2       # ~1.1 km cells for location matching

ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class BackfillError(RuntimeError):
    """Fatal, user-facing failure (message is printed, exit code non-zero)."""


class FetchError(BackfillError):
    def __init__(self, url: str, status: int | None, body: str):
        head = " ".join(body.strip().split())[:800]
        super().__init__(
            f"non-CSV/unusable response from FIRMS\n  url: {url}\n"
            f"  http status: {status}\n  body: {head or '(empty)'}"
        )


# ---------------------------------------------------------------------------
# Database resolution: fail LOUDLY instead of silently creating a SQLite file
# ---------------------------------------------------------------------------
DB_ENV_VARS = ("EMBEREBEYE_DATABASE_URL", "EMBEREBEYE_DB_URL")

_NO_DB_HELP = (
    "No database URL is set - both EMBEREBEYE_DATABASE_URL and\n"
    "EMBEREBEYE_DB_URL are empty, and this script refuses to guess.\n"
    "The v1.0.3 backfill silently wrote the real archive into\n"
    "./embereye_dev.db through the quiet SQLite fallback - never again.\n"
    "\n"
    "Pick ONE:\n"
    "  1) Postgres (the database the compose stack uses):\n"
    "       PowerShell: $env:EMBEREBEYE_DATABASE_URL = \"postgresql+psycopg://embereye:<pw>@localhost:5432/embereye\"\n"
    "       bash:       export EMBEREBEYE_DATABASE_URL=\"postgresql+psycopg://embereye:<pw>@localhost:5432/embereye\"\n"
    "  2) Explicit SQLite file (only if you really want the local file):\n"
    "       python scripts/backfill_firms.py --sqlite C:\\path\\to\\embereye_dev.db ...\n"
    "  3) Put EMBEREBEYE_DATABASE_URL=... into the repo .env (the script reads it).\n"
    "\n"
    "The resolved target (engine + host/path, password masked) is printed at\n"
    "startup of every run - always check that line before letting it fetch."
)


def resolve_db_url(sqlite_flag: str | None) -> tuple[str, str]:
    """-> (database_url, source_label). `--sqlite` wins over the env var.

    Raises BackfillError (with the fix-it instructions inside the message)
    when neither an env var nor the flag is present.
    """
    if sqlite_flag:
        path = Path(sqlite_flag).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return f"sqlite:///{path.as_posix()}", f"--sqlite {path}"
    for name in DB_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    raise BackfillError(_NO_DB_HELP)


def activate_db(args: argparse.Namespace) -> str:
    """Resolve the DB URL, inject it for app.config and print the banner.

    Must run BEFORE get_settings()/app.db are first used in the process.
    """
    url, source = resolve_db_url(args.sqlite)
    os.environ["EMBEREBEYE_DATABASE_URL"] = url
    from app.db import describe_url

    print(f"Database: {describe_url(url)}   [source: {source}]")
    return url


def raw_path_for(raw_dir: Path, product: str, c_start: date, c_end: date) -> Path:
    return raw_dir / product / f"{c_start.isoformat()}_{c_end.isoformat()}.csv"


def save_raw_body(raw_dir: Path, product: str, c_start: date, c_end: date,
                  body: str) -> bool:
    """Save one chunk's verbatim response body. Idempotent: an existing raw
    copy (resume/retry) is never overwritten. Returns True when written."""
    path = raw_path_for(raw_dir, product, c_start, c_end)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    return True


def satellites_of(sensors: list[str]) -> set[str]:
    """hotspots.satellite values owned by the given sensors (scoped --fresh)."""
    out: set[str] = set()
    for s in sensors:
        out |= SENSOR_SAT_SET[s]
    return out


def drop_sensor_products(state: "BackfillState", sensors: list[str]) -> int:
    """Forget resumable state for the given sensors' products only - a scoped
    --fresh must not touch the chunks of sensors it does not wipe."""
    products = {p for s in sensors for p in SENSORS[s]}
    removed = 0
    for product in products:
        removed += len(state.chunks.pop(product, {}))
    return removed


def confirm_destructive(prompt: str, assume_yes: bool) -> bool:
    """Print the prompt and require an explicit yes on the terminal."""
    if assume_yes:
        print(f"{prompt}\n  -> confirmed via --yes")
        return True
    try:
        answer = input(f"{prompt}\n  Type YES to continue (anything else aborts): ")
    except EOFError:
        print("\n  no interactive answer - aborting (use --yes to skip the prompt)")
        return False
    return answer.strip().upper() in {"YES", "Y"}


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Availability:
    ranges: dict[str, tuple[date, date]] = field(default_factory=dict)
    source: str = "fallback"            # live | fallback
    detail: str = ""

    def max_date(self, product: str) -> date:
        return self.ranges[product][1]

    def min_date(self, product: str) -> date:
        return self.ranges[product][0]


def parse_availability_csv(text: str) -> dict[str, tuple[date, date]]:
    """Tolerant parser for the FIRMS data_availability CSV.

    Accepts any column order: each line must contain a known product id and
    at least one ISO date (first date = min, last = max). Header lines and
    unrelated rows are skipped. Raises BackfillError on a non-CSV payload.
    """
    if not text.strip():
        raise BackfillError("data_availability returned an empty body")
    if "latitude" not in text.lstrip()[:200].lower() and not ISO_DATE_RE.search(text):
        raise BackfillError(
            f"data_availability response is not CSV: {text.strip()[:200]!r}")

    out: dict[str, tuple[date, date]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        product = next((p for p in PRODUCT_FAMILY if p.lower() in line.lower()), None)
        if product is None:
            continue
        dates = [date.fromisoformat(m) for m in ISO_DATE_RE.findall(line)]
        if not dates:
            continue
        lo, hi = min(dates), max(dates)
        if product in out:
            lo, hi = min(lo, out[product][0]), max(hi, out[product][1])
        out[product] = (lo, hi)
    if not out:
        raise BackfillError("data_availability CSV contained no known products")
    return out


def fetch_availability(cfg, timeout: float, retries: int, min_interval: float,
                       dry_run: bool, raw_dir: Path | None = None) -> Availability:
    """Live availability via the ALL endpoint; fallback table on any failure.

    For a real (non-dry) run a broken availability endpoint is fatal: the
    seam would silently rely on stale hardcoded dates. Dry-runs degrade to
    the fallback table and say so. A successful body is saved verbatim under
    raw_dir/_availability/ when raw_dir is given (provenance for the seams).
    """
    if not cfg.firms_map_key:
        return Availability(dict(FALLBACK_AVAILABILITY), "fallback",
                             "FIRMS_MAP_KEY not set - hardcoded 2026-11-01 table")
    url = (f"{cfg.firms_base_url.rstrip('/')}"
           f"/api/data_availability/csv/{cfg.firms_map_key}/ALL")
    try:
        _status, text = http_get(url, timeout, retries, min_interval)
        if raw_dir is not None:
            save_raw_body(raw_dir / "_availability", "ALL",
                          date.today(), date.today(), text)
        parsed = parse_availability_csv(text)
    except BackfillError as exc:
        if not dry_run:
            raise
        return Availability(
            dict(FALLBACK_AVAILABILITY), "fallback",
            f"live availability failed ({exc}); using hardcoded 2026-11-01 table")
    merged = dict(FALLBACK_AVAILABILITY)
    merged.update(parsed)
    missing = [p for p in PRODUCT_FAMILY if p not in parsed]
    detail = "live data_availability (ALL endpoint)"
    if missing:
        detail += f"; no rows for {', '.join(missing)} - fallback dates used"
    return Availability(merged, "live", detail)


# ---------------------------------------------------------------------------
# Window planning: the SP -> NRT seam
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ProductWindow:
    product: str
    start: date
    end: date
    n_chunks: int = 0

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(slots=True)
class SensorPlan:
    sensor: str
    sp: ProductWindow | None
    nrt: ProductWindow | None
    notes: list[str] = field(default_factory=list)

    @property
    def windows(self) -> list[ProductWindow]:
        return [w for w in (self.sp, self.nrt) if w is not None]


def build_windows(sensor: str, start: date, end: date, avail: Availability) -> SensorPlan:
    """SP up to its max_date, NRT from the day after - never assume SP covers
    through today. Clamps to each product's real availability and annotates
    every clamp/gap so the dry-run shows exactly what will be requested."""
    sp_product, nrt_product = SENSORS[sensor]
    notes: list[str] = []
    sp: ProductWindow | None = None
    nrt: ProductWindow | None = None

    sp_min, sp_max = avail.min_date(sp_product), avail.max_date(sp_product)
    nrt_min, nrt_max = avail.min_date(nrt_product), avail.max_date(nrt_product)

    # ---- SP window --------------------------------------------------------
    if start > sp_max:
        notes.append(f"start {start} is after {sp_product} max_date {sp_max}: no SP fetch")
    else:
        s0 = max(start, sp_min)
        if s0 != start:
            notes.append(f"SP start clamped to product availability ({sp_min})")
        e0 = min(end, sp_max)
        if e0 < end:
            notes.append(f"SP end clamped to SP max_date {sp_max} (rest comes from NRT)")
        if s0 <= e0:
            sp = ProductWindow(sp_product, s0, e0)

    # ---- NRT window: the day after SP max_date ----------------------------
    seam = sp_max + timedelta(days=1)
    s1 = max(start, seam, nrt_min)
    if nrt_min > seam:
        notes.append(
            f"coverage gap {seam}..{nrt_min - timedelta(days=1)}: "
            f"{sp_product} ends {sp_max}, {nrt_product} starts {nrt_min}")
    if s1 > seam:
        notes.append(f"NRT start clamped to requested start / NRT availability ({s1})")
    e1 = min(end, nrt_max)
    if e1 < end:
        notes.append(f"NRT end clamped to NRT max_date {nrt_max}")
    if s1 <= e1:
        nrt = ProductWindow(nrt_product, s1, e1)

    return SensorPlan(sensor, sp, nrt, notes)


def iter_chunks(start: date, end: date, size: int) -> list[tuple[date, date]]:
    """Deterministic day-aligned chunks of `size` days (last one may be short)."""
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=size - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def build_plans(sensors: list[str], start: date, end: date, avail: Availability,
                chunk_days: int) -> tuple[list[SensorPlan], int]:
    plans = [build_windows(s, start, end, avail) for s in sensors]
    for plan in plans:
        for w in plan.windows:
            w.n_chunks = len(iter_chunks(w.start, w.end, chunk_days))
    total = sum(w.n_chunks for p in plans for w in p.windows)
    return plans, total


def fetch_sequence(plan: SensorPlan) -> list[tuple[ProductWindow, bool]]:
    """Windows in fetch order, each with a newest-first chunk flag.

    Run plan (most time-critical first): SNPP, NOAA20, NOAA21 fetch SP then
    NRT - SP first builds that sensor's static-source list for its NRT rows.
    MODIS goes NRT first, then the 26-year SP walk newest -> oldest, per the
    v1.0.3 run plan; MODIS NRT rows are re-checked against the static list
    in a final DB pass once MODIS SP has been ingested.
    """
    if plan.sensor == "MODIS":
        seq: list[tuple[ProductWindow | None, bool]] = [
            (plan.nrt, False), (plan.sp, True)]
    else:
        seq = [(plan.sp, False), (plan.nrt, False)]
    return [(w, nf) for w, nf in seq if w is not None]


def chunk_url(cfg, product: str, bbox: str, c_start: date, c_end: date) -> str:
    """FIRMS area CSV URL for one chunk, anchored per the official contract.

    /api/area/csv/KEY/SOURCE/BBOX/DAY_RANGE/DATE returns
    [DATE .. DATE + DAY_RANGE - 1] (forward from DATE), DAY_RANGE capped at
    5. A chunk [c_start..c_end] therefore requests window days anchored at
    DATE = c_start - anchoring at c_end would silently fetch the days AFTER
    the chunk (the v1.0.2 anchoring bug).
    """
    days = (c_end - c_start).days + 1
    return (f"{cfg.firms_base_url.rstrip('/')}/api/area/csv/"
            f"{cfg.firms_map_key}/{product}/"
            f"{bbox}/{days}/{c_start.isoformat()}")


# ---------------------------------------------------------------------------
# HTTP with hard timeout, retries, Retry-After and rate-limit pacing
# ---------------------------------------------------------------------------
_last_request_at = 0.0


def http_get(url: str, timeout: float, retries: int, min_interval: float) -> tuple[int, str]:
    """GET with per-request timeout and bounded retries. Never hangs forever.

    Returns (status, body). Raises BackfillError only when the endpoint stays
    unreachable/5xx; non-CSV 200 bodies are checked by ensure_firms_csv().
    """
    global _last_request_at
    attempts = max(1, retries + 1)
    last_err = ""
    last_body = ""
    for attempt in range(attempts):
        wait = min_interval - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": USER_AGENT})
        except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                time.sleep(min(8.0, 1.5 * (2 ** attempt)))
            continue
        if resp.status_code == 200:
            return resp.status_code, resp.text
        last_body = resp.text
        if resp.status_code in (429, 503):
            retry_after = resp.headers.get("Retry-After")
            delay = (float(retry_after) if (retry_after or "").isdigit()
                     else min(30.0, 2.0 * (2 ** attempt)))
            last_err = f"HTTP {resp.status_code} (rate limited)"
            if attempt + 1 < attempts:
                time.sleep(delay)
            continue
        last_err = f"HTTP {resp.status_code}"
        if attempt + 1 < attempts:
            time.sleep(min(8.0, 1.5 * (2 ** attempt)))
    raise BackfillError(
        f"FIRMS request failed after {attempts} attempts ({last_err})"
        + (f"; last body: {' '.join(last_body.split())[:400]}" if last_body else ""))


def ensure_firms_csv(url: str, status: int, body: str) -> None:
    """Guard: the payload must look like a FIRMS area CSV export.

    Header-only responses are valid (a quiet window). Anything else -
    "Invalid MAP_KEY", rate-limit text, HTML error pages - is fatal and the
    body is printed so the cause is visible in the logs.
    """
    first = next((ln for ln in body.splitlines() if ln.strip()), "").strip().lower()
    ok = first.startswith("latitude") and (
        "brightness" in first or "bright_ti4" in first or "bright_ti5" in first)
    if not ok:
        raise FetchError(url, status, body)


# ---------------------------------------------------------------------------
# Resumable state (format v2) + legacy invalidation
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class BackfillState:
    aoi_id: str
    chunks: dict[str, dict[str, dict]] = field(default_factory=dict)
    availability_meta: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)   # migration/invalidation report

    # -- chunk bookkeeping --------------------------------------------------
    def mark(self, product: str, start: date, end: date, rows: int,
             status: str, attempts: int) -> None:
        self.chunks.setdefault(product, {})[start.isoformat()] = {
            "end": end.isoformat(), "rows": rows, "status": status,
            "attempts": attempts, "fetched_at": _now_iso(),
        }

    def done(self, product: str, start: date, end: date) -> dict | None:
        entry = self.chunks.get(product, {}).get(start.isoformat())
        if entry and entry.get("status") == "OK" and entry.get("end") == end.isoformat():
            return entry
        return None

    def count_done(self, product: str) -> int:
        return sum(1 for e in self.chunks.get(product, {}).values()
                   if e.get("status") == "OK")


def load_state(path: Path, aoi_id: str, avail: Availability,
               dry_run: bool) -> BackfillState:
    """Load state; migrate legacy formats; invalidate out-of-coverage chunks.

    Invalidation rule (the old script's corruption): any SP chunk whose end is
    beyond the product's SP max_date - a header-only response there is
    OUT_OF_COVERAGE, never quiet. Symmetrically, NRT chunks ending before the
    NRT min_date. Zero-row chunks are counted separately in the report.
    """
    state = BackfillState(aoi_id=aoi_id)
    report: dict = {"source": "new", "migrated": 0,
                    "invalidated_sp_beyond_max": 0,
                    "invalidated_sp_beyond_max_zero_rows": 0,
                    "invalidated_nrt_before_min": 0,
                    "invalidated_nrt_before_min_zero_rows": 0}
    if path.exists():
        try:
            raw = json.loads(path.read_text())
        except (ValueError, OSError) as exc:
            report["source"] = f"unreadable ({type(exc).__name__}); starting fresh"
            raw = None
        if isinstance(raw, dict) and raw.get("state_version") == 2:
            state.chunks = raw.get("chunks", {})
            report["source"] = "v2"
        elif isinstance(raw, dict):
            report["source"] = "legacy"
            report["migrated"] = _migrate_legacy(raw, state)
        elif raw is not None:
            report["source"] = "unrecognized; starting fresh"

    # -- apply coverage invalidation ----------------------------------------
    for product, chunks in list(state.chunks.items()):
        is_sp = product.endswith("_SP")
        is_nrt = product.endswith("_NRT")
        if not (is_sp or is_nrt):
            continue
        if is_sp:
            limit = avail.max_date(product)
            key, zero_key = ("invalidated_sp_beyond_max",
                             "invalidated_sp_beyond_max_zero_rows")
            bad = [k for k, e in chunks.items()
                   if date.fromisoformat(e["end"]) > limit]
        else:
            limit = avail.min_date(product)
            key, zero_key = ("invalidated_nrt_before_min",
                             "invalidated_nrt_before_min_zero_rows")
            bad = [k for k, e in chunks.items()
                   if date.fromisoformat(e["end"]) < limit]
        for k in bad:
            entry = chunks.pop(k)
            report[key] += 1
            if int(entry.get("rows", 0)) == 0:
                report[zero_key] += 1
        if bad:
            state.chunks[product] = chunks

    state.report = report
    if not dry_run and path.exists():
        save_state(state, path, avail)
    return state


def _walk_dicts(node):
    """Yield every dict in a JSON tree (state files nest product maps under
    arbitrary wrapper keys like 'chunks' or 'products')."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_dicts(value)


def _migrate_legacy(raw: dict, state: BackfillState) -> int:
    """Best-effort scan of the v1 state file: any dict keyed by a known FIRMS
    product anywhere in the tree is treated as a chunk map. Chunk keys may
    hold start-only or start_end dates; entries may be dicts
    (rows/row_count/n_rows/count, optional end) or bare integers."""
    migrated = 0
    seen: set[tuple[str, str]] = set()
    for node in _walk_dicts(raw):
        for key, value in node.items():
            product = next((p for p in PRODUCT_FAMILY
                            if p.lower() in str(key).lower()), None)
            if product is None or not isinstance(value, dict):
                continue
            for chunk_key, entry in value.items():
                key_dates = ISO_DATE_RE.findall(str(chunk_key))
                if isinstance(entry, dict):
                    start_dates = ISO_DATE_RE.findall(str(entry.get("start", "")))
                    end_dates = ISO_DATE_RE.findall(str(entry.get("end", "")))
                    rows = entry.get("rows", entry.get("row_count",
                                     entry.get("n_rows", entry.get("count", 0))))
                    status = str(entry.get("status", "OK")).upper()
                else:
                    start_dates, end_dates = [], []
                    rows, status = entry, "OK"
                all_dates = key_dates + start_dates
                if not all_dates:
                    continue
                try:
                    start = date.fromisoformat(all_dates[0])
                    end = (date.fromisoformat(end_dates[-1]) if end_dates
                           else date.fromisoformat(all_dates[-1]))
                except ValueError:
                    continue
                if status != "OK":
                    continue
                if (product, start.isoformat()) in seen:
                    continue
                seen.add((product, start.isoformat()))
                state.chunks.setdefault(product, {})[start.isoformat()] = {
                    "end": end.isoformat(), "rows": int(rows or 0),
                    "status": "OK", "attempts": 0, "fetched_at": "legacy",
                }
                migrated += 1
    return migrated


def save_state(state: BackfillState, path: Path, avail: Availability) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state_version": 2,
        "script_version": SCRIPT_VERSION,
        "aoi_id": state.aoi_id,
        "updated_at": _now_iso(),
        "availability": state.availability_meta,
        "chunks": state.chunks,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# CSV -> canonical records
# ---------------------------------------------------------------------------
def parse_and_tag(text: str, aoi_id: str, product: str):
    """Parse one FIRMS CSV through the canonical parser, tagging SP/NRT."""
    from app.ingest.parser import parse_firms_csv

    family = PRODUCT_FAMILY[product]
    prod_tag = "SP" if product.endswith("_SP") else "NRT"
    return parse_firms_csv(text, aoi_id, family, product=prod_tag)


def add_sp_static_locations(records, static_locs: set[tuple[float, float]]) -> int:
    """Collect type=2 SP locations (rounded) and flag those rows."""
    added = 0
    for rec in records:
        if rec.firms_type == STATIC_SOURCE_TYPE:
            static_locs.add((round(rec.lat, STATIC_ROUND_DECIMALS),
                             round(rec.lon, STATIC_ROUND_DECIMALS)))
            rec.static_source = True
            added += 1
    return added


def apply_static_locations(records, static_locs: set[tuple[float, float]]) -> int:
    """Flag NRT rows falling on known static-source cells (type column absent)."""
    hits = 0
    for rec in records:
        if rec.static_source:
            continue
        if (round(rec.lat, STATIC_ROUND_DECIMALS),
                round(rec.lon, STATIC_ROUND_DECIMALS)) in static_locs:
            rec.static_source = True
            hits += 1
    return hits


def db_static_locations(db, aoi_id: str) -> set[tuple[float, float]]:
    """Static cells already known from persisted SP rows (resumed runs)."""
    from sqlalchemy import select

    from app.models import Hotspot

    rows = db.execute(
        select(Hotspot.lat, Hotspot.lon).where(
            Hotspot.aoi_id == aoi_id, Hotspot.firms_type == STATIC_SOURCE_TYPE)
    ).all()
    return {(round(lat, STATIC_ROUND_DECIMALS), round(lon, STATIC_ROUND_DECIMALS))
            for lat, lon in rows}


def rows_outside_window(records, c_start: date, c_end: date) -> list[date]:
    """acq_dates of rows OUTSIDE the requested chunk window.

    The API is supposed to return exactly [c_start..c_end]; rows outside mean
    the anchor direction or window is wrong and the archive would be
    silently corrupted - measured on every chunk, abort past 50%.
    """
    return [r.acq_date for r in records if not (c_start <= r.acq_date <= c_end)]


def truncation_hint(records, c_start: date, c_end: date) -> str | None:
    """Warn heuristic: rows exist but span fewer days than requested AND sit
    near the window end - consistent with the server clipping the start of
    the window (e.g. a DAY_RANGE larger than it honours). Fires are episodic,
    so this is a hint printed as WARN, never an error."""
    if not records:
        return None
    days = {r.acq_date for r in records}
    window_days = (c_end - c_start).days + 1
    if len(days) >= window_days:
        return None
    if max(days) >= c_end - timedelta(days=1):
        return (f"rows span {min(days)}..{max(days)} of requested "
                f"{c_start}..{c_end} and touch the window end - "
                f"possible truncated window start")
    return None


def row_signature(records) -> list[tuple]:
    """Order-independent row identity for the chunk-equivalence check."""
    return sorted((r.lat, r.lon, r.acq_dt_utc.isoformat(timespec="minutes"),
                   r.satellite) for r in records)


def reapply_static_locations(db, aoi_id: str,
                             static_locs: set[tuple[float, float]]) -> int:
    """DB pass: flag NRT rows at SP type=2 cells (firms_type stays NULL).

    Catches rows ingested before their sensor's SP window - with the v1.0.3
    order MODIS NRT is fetched before the MODIS SP walk, so those rows can
    only be matched after SP completes. Same rounding as the ingest-time
    match; NRT rows keep firms_type NULL, the flag is what matters.
    """
    if not static_locs:
        return 0
    from sqlalchemy import select, update

    from app.models import Hotspot

    rows = db.execute(
        select(Hotspot.id, Hotspot.lat, Hotspot.lon).where(
            Hotspot.aoi_id == aoi_id, Hotspot.firms_product == "NRT",
            Hotspot.static_source.is_(False))
    ).all()
    ids = [rid for rid, lat, lon in rows
           if (round(lat, STATIC_ROUND_DECIMALS),
               round(lon, STATIC_ROUND_DECIMALS)) in static_locs]
    for start in range(0, len(ids), 10000):
        db.execute(update(Hotspot)
                   .where(Hotspot.id.in_(ids[start:start + 10000]))
                   .values(static_source=True))
    db.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    # Database FIRST - fail loudly (exit 2) before touching FIRMS when the
    # target is unknown. Injects the URL for app.config; get_settings() below
    # then sees the resolved value. The only exemption is the pure-planning
    # --dry-run --no-db combination, which never opens a database.
    if args.dry_run and args.no_db:
        print("Database: not used (--dry-run --no-db)")
    else:
        try:
            activate_db(args)
        except BackfillError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    cfg = get_settings()
    if not cfg.firms_map_key and not args.dry_run:
        print("ERROR: FIRMS_MAP_KEY is not set (export it or put it in backend/.env).",
              file=sys.stderr)
        return 3

    # ---- database (optional in dry-run) -----------------------------------
    db = None
    if not (args.dry_run and args.no_db):
        try:
            from sqlalchemy import text as sql_text

            from app.db import SessionLocal, create_all

            create_all()  # also runs the additive schema migration
            db = SessionLocal()
            db.execute(sql_text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            if args.dry_run:
                print(f"NOTE: database unavailable for dry-run stats ({exc})\n")
                db = None
            else:
                print(f"ERROR: cannot open database: {exc}", file=sys.stderr)
                return 3

    # ---- availability + plans ----------------------------------------------
    print(f"EmberEye FIRMS backfill script v{SCRIPT_VERSION} (app v{EMBEREBYE_VERSION})")
    print(f"  window : {args.start} .. {args.end}"
          + ("  (WARNING: end is today - the last FIRMS day may be partial)"
             if args.end >= date.today() else ""))
    print(f"  aoi    : {args.aoi}   sensors: {','.join(args.sensors)}")
    print(f"  chunk  : {args.chunk_days} days   min request interval: {args.min_interval}s\n")

    raw_dir = (Path(args.raw_dir) if args.raw_dir
               else Path(args.state).parent / "raw")
    avail = fetch_availability(cfg, args.timeout, args.retries, args.min_interval,
                               args.dry_run, raw_dir=raw_dir)
    print(f"Availability: {avail.source.upper()} - {avail.detail}")
    for sensor in args.sensors:
        for product in SENSORS[sensor]:
            lo, hi = avail.ranges[product]
            print(f"  {product:<18} {lo} .. {hi}")
    print()

    plans, total_requests = build_plans(args.sensors, args.start, args.end, avail,
                                        args.chunk_days)

    # ---- state: load, migrate, invalidate ----------------------------------
    state_path = Path(args.state)
    state = load_state(state_path, args.aoi, avail, args.dry_run)
    state.availability_meta = {
        "source": avail.source, "fetched_at": _now_iso(),
        "ranges": {p: [lo.isoformat(), hi.isoformat()]
                   for p, (lo, hi) in avail.ranges.items()},
    }
    print(f"State: {state_path}")
    print(f"  {state.report['source']} format; migrated={state.report['migrated']}, "
          f"invalidated SP chunks beyond SP max_date="
          f"{state.report['invalidated_sp_beyond_max']} "
          f"(0-row: {state.report['invalidated_sp_beyond_max_zero_rows']}), "
          f"invalidated NRT chunks before NRT min_date="
          f"{state.report['invalidated_nrt_before_min']} "
          f"(0-row: {state.report['invalidated_nrt_before_min_zero_rows']})")

    # ---- dry-run report -----------------------------------------------------
    print("\nPer-sensor windows (fetch order, most time-critical first):")
    for plan in plans:
        print(f"  {plan.sensor} [{SENSOR_SATELLITES[plan.sensor]}]")
        for w, nf in fetch_sequence(plan):
            done = state.count_done(w.product)
            order = "newest -> oldest" if nf else "oldest -> newest"
            print(f"    {w.product:<18} {w.start} .. {w.end}  "
                  f"({w.days:>5} days, {w.n_chunks:>4} requests, {done} done, {order})")
        for note in plan.notes:
            print(f"      note: {note}")
    done_total = sum(state.count_done(w.product) for p in plans for w in p.windows)
    todo = total_requests - done_total
    est_min = todo * args.min_interval / 60.0
    print(f"\nRequests: {total_requests} total, {done_total} completed, "
          f"{todo} to fetch (~{est_min:.0f} min pacing alone at "
          f"{args.min_interval}s, plus transfer)")

    if args.dry_run:
        print("\nDRY-RUN: nothing was fetched or written.")
        if db is not None:
            db.close()
        return 0

    # ---- safety checks before touching data ---------------------------------
    if db is None:
        print("ERROR: database unavailable for a real run.", file=sys.stderr)
        return 3
    from sqlalchemy import delete, func, select

    from app.models import Aoi, Hotspot
    from app.seed.pipeline import ensure_preset_aois

    aoi = db.get(Aoi, args.aoi)
    if aoi is None:
        ensure_preset_aois(db)
        aoi = db.get(Aoi, args.aoi)
    if aoi is None:
        print(f"ERROR: AOI '{args.aoi}' does not exist and is not a preset.",
              file=sys.stderr)
        return 3

    existing = int(db.scalar(select(func.count()).select_from(Hotspot)
                             .where(Hotspot.aoi_id == aoi.id)) or 0)
    fresh_state = state.report["source"] in {
        "new", "unrecognized; starting fresh",
    } or not state_path.exists()
    if args.fresh:
        # ---- scoped --fresh (the v1.0.3 footgun fix) ----------------------
        # `--fresh --sensors NOAA20,NOAA21` used to wipe EVERY satellite of
        # the AOI (93k S-NPP rows lost). Now: --fresh deletes only the
        # satellites owned by --sensors; --fresh-all-sensors is the explicit
        # everything-wipe. Confirmation shows the exact row counts first.
        all_sats = bool(args.fresh_all_sensors)
        sat_set = None if all_sats else satellites_of(args.sensors)
        del_filter = (Hotspot.aoi_id == aoi.id if all_sats
                      else (Hotspot.aoi_id == aoi.id)
                      & Hotspot.satellite.in_(sorted(sat_set)))
        per_sat = dict(db.execute(
            select(Hotspot.satellite, func.count())
            .where(Hotspot.aoi_id == aoi.id).group_by(Hotspot.satellite)
        ).all())
        to_delete = (sum(per_sat.values()) if all_sats
                     else sum(per_sat.get(s, 0) for s in (sat_set or set())))
        kept_sats = {s: n for s, n in per_sat.items()
                     if not all_sats and s not in (sat_set or set())}
        scope = ("ALL satellites (--fresh-all-sensors)" if all_sats
                 else "satellites of --sensors: " + ", ".join(sorted(sat_set or set())))
        print(f"\n--fresh will DELETE {to_delete:,} hotspots rows for AOI "
              f"'{aoi.id}' ({scope});")
        for s in sorted(per_sat):
            mark = ("DELETE" if all_sats or s in (sat_set or set()) else "keep  ")
            print(f"    {s:<6} {per_sat[s]:>9,}  {mark}")
        if kept_sats:
            print(f"    kept (NOT touched): "
                  + ", ".join(f"{s}={n:,}" for s, n in sorted(kept_sats.items()))
                  + "  (use --fresh-all-sensors to remove these too)")
        print("    raw chunk copies under data/firms_backfill/raw/ are NOT deleted.")
        if to_delete and not confirm_destructive(
                f"Delete {to_delete:,} rows and reset the affected resumable state?",
                assume_yes=args.yes):
            print("Aborted by the user; nothing was deleted.")
            db.close()
            return 3
        wiped = db.execute(delete(Hotspot).where(del_filter)).rowcount
        db.commit()
        if all_sats:
            state = BackfillState(aoi_id=aoi.id)
        else:
            dropped = drop_sensor_products(state, args.sensors)
            print(f"--fresh: wiped {wiped:,} rows for {aoi.id}; "
                  f"state reset for {','.join(args.sensors)} ({dropped} chunks), "
                  f"other sensors' state kept")
        state.availability_meta = {
            "source": avail.source, "fetched_at": _now_iso(),
            "ranges": {p: [lo.isoformat(), hi.isoformat()]
                       for p, (lo, hi) in avail.ranges.items()},
        }
    elif args.wipe_window:
        ws, we = (date.fromisoformat(x) for x in args.wipe_window.split("..", 1))
        wiped = db.execute(delete(Hotspot).where(
            Hotspot.aoi_id == aoi.id,
            Hotspot.local_date >= ws, Hotspot.local_date <= we)).rowcount
        db.commit()
        print(f"\n--wipe-window: deleted {wiped} rows for {aoi.id} in {ws}..{we}")
    elif existing and fresh_state and not args.keep_existing:
        print(
            f"\nABORT: AOI '{aoi.id}' already holds {existing} rows and no resumable "
            "state was found.\n"
            "Choose explicitly:\n"
            "  --fresh                    wipe the rows of --sensors' satellites,\n"
            "                             then backfill (asks for confirmation;\n"
            "                             add --fresh-all-sensors to wipe every satellite)\n"
            "  --keep-existing            add real rows next to the existing ones\n"
            "  --wipe-window S..E         delete one date range, then resume",
            file=sys.stderr)
        db.close()
        return 3

    # ---- ingest --------------------------------------------------------------
    from app.ingest.dedup import deduplicate
    from app.ingest.persist import (apply_static_source_mask, create_run,
                                    finish_run, persist_records)

    run_row = create_run(db, mode="LIVE", source="firms_backfill", aoi_id=aoi.id)
    static_locs = db_static_locations(db, aoi.id)
    print(f"\nStatic-source cells seeded from DB: {len(static_locs)} "
          f"(round {STATIC_ROUND_DECIMALS} decimals)")

    bbox = f"{aoi.west:.4f},{aoi.south:.4f},{aoi.east:.4f},{aoi.north:.4f}"
    totals = {"rows_in": 0, "kept": 0, "deduped": 0, "rejected": 0,
              "static_flagged": 0, "requests": 0, "quiet_chunks": 0,
              "warnings": 0, "raw_saved": 0}
    print(f"Raw chunk copies -> {raw_dir} (verbatim, survive --fresh)")
    sensor_rows: dict[str, dict[str, int]] = {
        s: {"rows_in": 0, "kept": 0} for s in args.sensors}

    pending: list[tuple[str, ProductWindow, tuple[date, date]]] = []
    for plan in plans:
        for w, newest_first in fetch_sequence(plan):
            chunks = iter_chunks(w.start, w.end, args.chunk_days)
            if newest_first:
                chunks.reverse()
            pending.extend((plan.sensor, w, c) for c in chunks
                           if not state.done(w.product, c[0], c[1]))
    chunks_todo = len(pending)
    print(f"\nIngesting {chunks_todo} chunks - order: "
          f"{', '.join(args.sensors)} (MODIS: NRT first, SP newest->oldest)")
    run_t0 = time.monotonic()

    def _fmt_hms(seconds: float) -> str:
        total = max(0, int(seconds))
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"

    try:
        for n_done, (sensor, w, (c_start, c_end)) in enumerate(pending, start=1):
            url = chunk_url(cfg, w.product, bbox, c_start, c_end)
            status_code, body = http_get(url, args.timeout, args.retries,
                                         args.min_interval)
            ensure_firms_csv(url, status_code, body)   # non-CSV -> abort, print body
            totals["requests"] += 1
            if save_raw_body(raw_dir, w.product, c_start, c_end, body):
                totals["raw_saved"] += 1

            records, rejected, total_n, _samples = parse_and_tag(
                body, aoi.id, w.product)

            # -- per-chunk sanity (A: truncation + anchor verification) ----
            outside = rows_outside_window(records, c_start, c_end)
            if outside:
                totals["warnings"] += 1
                head = ", ".join(d.isoformat() for d in sorted(set(outside))[:4])
                print(f"    WARN [{w.product}] {c_start}..{c_end}: "
                      f"{len(outside)}/{total_n} rows outside the requested "
                      f"window ({head}...)", flush=True)
                if len(outside) * 2 > max(1, total_n):
                    raise FetchError(url, status_code,
                                     f"majority of rows outside the requested "
                                     f"window {c_start}..{c_end} - anchor or "
                                     f"direction mismatch, aborting to protect "
                                     f"the archive")
            hint = truncation_hint(records, c_start, c_end)
            if hint:
                totals["warnings"] += 1
                print(f"    WARN [{w.product}] {c_start}..{c_end}: {hint}",
                      flush=True)

            if w.product.endswith("_SP"):
                totals["static_flagged"] += add_sp_static_locations(records, static_locs)
            else:
                totals["static_flagged"] += apply_static_locations(records, static_locs)
            kept, deduped = deduplicate(
                records, cfg.dedup_max_gap_minutes,
                cfg.dedup_max_distance_km_modis, cfg.dedup_max_distance_km_viirs)
            persist_records(db, run_row.id, kept)

            totals["rows_in"] += total_n
            totals["kept"] += len(kept)
            totals["deduped"] += deduped
            totals["rejected"] += rejected
            quiet = total_n == 0
            totals["quiet_chunks"] += int(quiet)
            state.mark(w.product, c_start, c_end, len(kept), "OK", attempts=1)
            save_state(state, state_path, avail)
            sensor_rows[sensor]["rows_in"] += total_n
            sensor_rows[sensor]["kept"] += len(kept)

            elapsed = time.monotonic() - run_t0
            eta = elapsed / n_done * (chunks_todo - n_done)
            finish = datetime.now() + timedelta(seconds=eta)
            pct = 100.0 * n_done / max(1, chunks_todo)
            tag = "QUIET" if quiet else f"rows={total_n:>5}"
            print(f"    [{sensor}/{w.product} {n_done}/{chunks_todo} {pct:3.0f}%] "
                  f"{c_start}..{c_end} {tag} kept={len(kept):>5} "
                  f"rej={rejected:<3} dedup={deduped:<4} "
                  f"| {sensor} rows {sensor_rows[sensor]['rows_in']:,} "
                  f"| elapsed {_fmt_hms(elapsed)} "
                  f"ETA {_fmt_hms(eta)} (~{finish:%H:%M} local)", flush=True)
    except BackfillError as exc:
        save_state(state, state_path, avail)
        from app.ingest.persist import finish_run as _fr

        _fr(db, run_row, "FAILED", str(exc)[:500], totals["rows_in"],
            totals["kept"], totals["deduped"], totals["rejected"])
        print(f"\nFATAL: {exc}", file=sys.stderr)
        print("State saved; re-run the same command to resume.", file=sys.stderr)
        db.close()
        return 2

    # ---- static re-check for rows fetched before their sensor's SP window ---
    reflagged = reapply_static_locations(db, aoi.id, static_locs)
    if reflagged:
        totals["static_flagged"] += reflagged
        print(f"\nStatic-source re-check after SP completion: "
              f"{reflagged} NRT rows flagged")

    # ---- static mask (persistence pass) + harmonization pipeline -------------
    flagged = 0
    if not args.no_static_mask:
        flagged = apply_static_source_mask(
            db, aoi.id, cfg.static_source_min_days, cfg.static_source_cell_deg)
        print(f"\nPersistence static-source mask: {flagged} additional rows flagged")
    if not args.no_pipeline:
        from app.harmonize.pipeline import run_pipeline_for_aoi

        run_pipeline_for_aoi(db, aoi)
        print("Harmonization pipeline rebuilt for the AOI.")

    from app.ingest.persist import finish_run

    finish_run(db, run_row, "OK", f"backfill complete ({totals['requests']} requests)",
               totals["rows_in"], totals["kept"], totals["deduped"],
               totals["rejected"],
               meta={"static_ingest_flagged": totals["static_flagged"],
                     "static_persistence_flagged": flagged,
                     "availability": avail.source,
                     "quiet_chunks": totals["quiet_chunks"],
                     "raw_saved": totals["raw_saved"]})
    save_state(state, state_path, avail)
    per_sensor = ", ".join(f"{s}={v['kept']:,}" for s, v in sensor_rows.items())
    print(f"\nDONE: rows_in={totals['rows_in']:,} kept={totals['kept']:,} "
          f"deduped={totals['deduped']:,} rejected={totals['rejected']:,} "
          f"static_flagged={totals['static_flagged'] + flagged:,} "
          f"quiet_chunks={totals['quiet_chunks']} "
          f"requests={totals['requests']:,} warnings={totals['warnings']} "
          f"raw_saved={totals['raw_saved']:,}")
    print(f"      kept per sensor: {per_sensor}")
    db.close()
    return 0


# ---------------------------------------------------------------------------
# --status: what is ACTUALLY in the database in use (no fetch, no writes)
# ---------------------------------------------------------------------------
SNAPSHOT_COLUMNS = (
    "id", "aoi_id", "satellite", "sensor_family", "instrument", "resolution_m",
    "acq_dt_utc", "acq_date", "local_date", "lat", "lon",
    "brightness_t4_k", "brightness_t11_k", "frp_mw",
    "confidence_class", "confidence_raw", "daynight",
    "scan_km", "track_km", "pixel_area_km2",
    "saturated", "band21_fallback", "static_source",
    "source_version", "firms_product", "firms_type",
)


def cmd_status(args: argparse.Namespace) -> int:
    """Per-satellite and per-product row counts, min/max dates, static and
    firms_type breakdowns - read-only, from the DB the run would use."""
    try:
        activate_db(args)
    except BackfillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from sqlalchemy import func, select

    from app.db import SessionLocal, create_all
    from app.models import (DailyAggregate, FireEpisode, HarmonizedDaily,
                            Hotspot)

    try:
        create_all()
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001 - wrong host/port refused cleanly
        print(f"ERROR: cannot open database: {exc}", file=sys.stderr)
        return 3
    try:
        total = int(db.scalar(select(func.count()).select_from(Hotspot)) or 0)
        print(f"\nDatabase status (read-only) - total hotspots: {total:,}")

        aois = db.execute(
            select(Hotspot.aoi_id, func.count()).group_by(Hotspot.aoi_id)
        ).all()
        if len(aois) > 1:
            print("AOIs: " + ", ".join(f"{a}={n:,}" for a, n in aois)
                  + "  (report below is ALL AOIs combined)")

        print("\nPer satellite:")
        print(f"  {'sat':<7} {'rows':>9}  {'acq_date min':<12} {'acq_date max':<12} "
              f"{'static':>8} {'type0':>7} {'type1':>6} {'type2':>7} {'typeNULL':>8} "
              f"{'SP':>8} {'NRT':>7}")
        sat_rows = db.execute(
            select(Hotspot.satellite, func.count(),
                   func.min(Hotspot.acq_date), func.max(Hotspot.acq_date))
            .group_by(Hotspot.satellite)
        ).all()
        sat_static = dict(db.execute(
            select(Hotspot.satellite, func.count())
            .where(Hotspot.static_source.is_(True))
            .group_by(Hotspot.satellite)).all())
        sat_prod = {(s, p): int(n) for s, p, n in db.execute(
            select(Hotspot.satellite, Hotspot.firms_product, func.count())
            .group_by(Hotspot.satellite, Hotspot.firms_product)).all()}
        sat_types = db.execute(
            select(Hotspot.satellite, Hotspot.firms_type, func.count())
            .group_by(Hotspot.satellite, Hotspot.firms_type)).all()
        type_map: dict[str, dict[str, int]] = {}
        for sat, ftype, n in sat_types:
            key = "NULL" if ftype is None else str(ftype)
            type_map.setdefault(sat, {})[key] = int(n)
        for sat, n, dmin, dmax in sorted(sat_rows):
            t = type_map.get(sat, {})
            print(f"  {sat:<7} {n:>9,}  {dmin}     {dmax}     "
                  f"{sat_static.get(sat, 0):>8,} "
                  f"{t.get('0', 0):>7,} {t.get('1', 0):>6,} {t.get('2', 0):>7,} "
                  f"{t.get('NULL', 0):>8,} "
                  f"{sat_prod.get((sat, 'SP'), 0):>8,} "
                  f"{sat_prod.get((sat, 'NRT'), 0):>7,}")

        print("\nPer FIRMS product era (SP = science quality, NRT = near-real-time):")
        print(f"  {'product':<8} {'rows':>9}  {'acq_date min':<12} {'acq_date max':<12} "
              f"{'satellites'}")
        prod_rows = db.execute(
            select(Hotspot.firms_product, func.count(),
                   func.min(Hotspot.acq_date), func.max(Hotspot.acq_date))
            .group_by(Hotspot.firms_product)
        ).all()
        prod_sats: dict[str, set[str]] = {}
        for sat, prod, _n in db.execute(
                select(Hotspot.satellite, Hotspot.firms_product, func.count())
                .group_by(Hotspot.satellite, Hotspot.firms_product)).all():
            prod_sats.setdefault(prod or "(seed)", set()).add(sat)
        for prod, n, dmin, dmax in sorted(prod_rows, key=lambda r: (r[0] or "")):
            label = prod or "(seed/synthetic)"
            sats = ",".join(sorted(prod_sats.get(label, set())))
            print(f"  {label:<8} {n:>9,}  {dmin}     {dmax}     {sats}")

        print("\nDerived tables:")
        for name, model in (("daily_aggregates", DailyAggregate),
                            ("harmonized_daily", HarmonizedDaily),
                            ("fire_episodes", FireEpisode)):
            n = int(db.scalar(select(func.count()).select_from(model)) or 0)
            print(f"  {name:<20} {n:>9,}")

        if total == 0:
            print("\nNOTE: this database has NO hotspots - if you expected the real "
                  "backfill, you are probably pointing at the wrong target "
                  "(check the Database: line above).")
        return 0
    finally:
        db.close()


def cmd_build_snapshot(args: argparse.Namespace) -> int:
    """Export hotspots from the DB in use to data/firms_backfill/snapshots/.

    Honest scope: this exports DB ROWS (the canonical record). Verbatim raw
    FIRMS CSVs exist only for chunks fetched after v1.0.4 (see docs/DATA.md)
    - the snapshot reports how many state chunks have a raw copy on disk.
    """
    try:
        activate_db(args)
    except BackfillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    import csv as _csv

    from sqlalchemy import select

    from app.db import SessionLocal, create_all
    from app.models import Hotspot

    try:
        create_all()
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001 - wrong host/port refused cleanly
        print(f"ERROR: cannot open database: {exc}", file=sys.stderr)
        return 3
    try:
        q = select(*[getattr(Hotspot, c) for c in SNAPSHOT_COLUMNS]).order_by(
            Hotspot.aoi_id, Hotspot.acq_date, Hotspot.id)
        if not args.all_presets:
            q = q.where(Hotspot.aoi_id == args.aoi)
        out_dir = Path(args.state).parent / "snapshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = out_dir / f"hotspots_{stamp}.csv"
        n = 0
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = _csv.writer(fh)
            writer.writerow(SNAPSHOT_COLUMNS)
            for row in db.execute(q).yield_per(20000):
                writer.writerow(["" if v is None else v for v in row])
                n += 1
        raw_files = sum(1 for _ in Path(args.state).parent.joinpath("raw")
                        .rglob("*.csv")) if Path(args.state).parent.exists() else 0
        size_mb = out_path.stat().st_size / 1e6
        print(f"Snapshot: {out_path}  ({n:,} rows, {size_mb:.1f} MB)")
        print(f"Raw chunk copies on disk (post-v1.0.4 only): {raw_files}")
        print("See docs/DATA.md: the rows currently in the DB were fetched "
              "before raw copies existed - this snapshot is the row-level "
              "backup of that data.")
        return 0
    finally:
        db.close()



def parse_args(argv: list[str]) -> argparse.Namespace:
    today = date.today()
    ap = argparse.ArgumentParser(
        description="Resumable FIRMS backfill with SP/NRT seam handling.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START,
                    help="window start (UTC); default 2000-11-01 = MODIS_SP "
                         "min_date (Terra) per the official challenge scope")
    ap.add_argument("--end", type=date.fromisoformat,
                    default=today - timedelta(days=1),
                    help="window end (UTC); default yesterday to avoid partial days")
    ap.add_argument("--aoi", default="nile-delta-egypt",
                    help="AOI id to backfill (preset or user AOI)")
    ap.add_argument("--all-presets", action="store_true",
                    help="backfill every preset AOI, each with its own state file")
    ap.add_argument("--sensors", default=",".join(DEFAULT_SENSOR_ORDER),
                    help="comma list from: " + ",".join(SENSORS)
                         + " - fetched in the order given")
    ap.add_argument("--chunk-days", type=int, default=MAX_CHUNK_DAYS,
                    help="days per request; official FIRMS area API cap is 5")
    ap.add_argument("--min-interval", type=float, default=1.0,
                    help="minimum seconds between FIRMS requests (rate limiting)")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="per-request timeout in seconds")
    ap.add_argument("--retries", type=int, default=3,
                    help="retries per request (backoff + Retry-After)")
    ap.add_argument("--state",
                    default=str(REPO_ROOT / "data" / "firms_backfill" / "state.json"),
                    help="resumable state file (host-side data/firms_backfill)")
    ap.add_argument("--raw-dir", metavar="DIR", default=None,
                    help="where verbatim raw chunk responses are saved "
                         "(default: <state dir>/raw)")
    ap.add_argument("--sqlite", metavar="PATH", default=None,
                    help="EXPLICIT SQLite database file - the only sanctioned "
                         "way to run without EMBEREBEYE_DATABASE_URL (no "
                         "silent fallback exists anymore)")
    ap.add_argument("--status", action="store_true",
                    help="print per-satellite/per-product row counts, min/max "
                         "dates, static and firms_type breakdowns from the DB "
                         "in use, then exit (no fetch, no writes)")
    ap.add_argument("--build-snapshot", action="store_true",
                    help="export the AOI's hotspots from the DB in use to "
                         "data/firms_backfill/snapshots/ as CSV, then exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="show windows/requests/invalidation, fetch nothing")
    ap.add_argument("--verify-chunking", action="store_true",
                    help="live check before a long run: one N-day request must "
                         "reproduce the N x 1-day union; reports anchor direction")
    ap.add_argument("--verify-days", type=int, default=MAX_CHUNK_DAYS,
                    help="window size for --verify-chunking (2..5)")
    ap.add_argument("--verify-end", type=date.fromisoformat, default=date(2025, 10, 20),
                    help="window end for --verify-chunking (known-active period)")
    ap.add_argument("--verify-product", default="VIIRS_SNPP_SP",
                    help="product for --verify-chunking")
    ap.add_argument("--invalidate-only", action="store_true",
                    help="apply state invalidation to disk and exit (no fetch)")
    ap.add_argument("--fresh", action="store_true",
                    help="wipe the rows of --sensors' satellites (asks for "
                         "confirmation with exact counts), reset those "
                         "sensors' resumable state, then backfill")
    ap.add_argument("--fresh-all-sensors", action="store_true",
                    help="extend --fresh to EVERY satellite of the AOI "
                         "(the old unscoped behaviour, now explicit opt-in)")
    ap.add_argument("--yes", action="store_true",
                    help="answer YES to the --fresh confirmation prompt")
    ap.add_argument("--keep-existing", action="store_true",
                    help="allow adding rows to an AOI that already has data")
    ap.add_argument("--wipe-window", metavar="START..END", default=None,
                    help="delete the AOI's rows in a date range before resuming")
    ap.add_argument("--no-static-mask", action="store_true",
                    help="skip the persistence-based static-source pass")
    ap.add_argument("--no-pipeline", action="store_true",
                    help="skip the harmonization rebuild after ingest")
    ap.add_argument("--no-db", action="store_true",
                    help="(dry-run) skip the database entirely")
    args = ap.parse_args(argv)

    args.sensors = [s.strip().upper() for s in args.sensors.split(",") if s.strip()]
    unknown = [s for s in args.sensors if s not in SENSORS]
    if unknown:
        ap.error(f"unknown sensor(s): {unknown}; choose from {list(SENSORS)}")
    if args.chunk_days < 1 or args.chunk_days > MAX_CHUNK_DAYS:
        ap.error(f"--chunk-days must be 1..{MAX_CHUNK_DAYS} (official FIRMS "
                 "area API cap: DAY_RANGE is 1..5)")
    if args.start > args.end:
        ap.error("--start is after --end")
    if args.end >= today:
        print("WARNING: --end includes today; the last FIRMS day is partial "
              "(NRT publishes with a few hours' delay).", file=sys.stderr)
    if args.verify_chunking and not 2 <= args.verify_days <= MAX_CHUNK_DAYS:
        ap.error(f"--verify-days must be 2..{MAX_CHUNK_DAYS} (1-day anchors "
                 "cannot detect the direction)")
    return args


# ---------------------------------------------------------------------------
# Chunk-equivalence verification (live, cheap, run once before a long backfill)
# ---------------------------------------------------------------------------
def run_verify(args: argparse.Namespace) -> int:
    """Prove the area API contract on live data before a long run.

    For a known-active N-day window (default VIIRS_SNPP_SP, 2025-10-16..20):
      A  one request anchored at the window START (docs: [DATE..DATE+N-1])
      C  one request anchored at the window END   (direction probe)
      B  N separate 1-day requests (ground truth)
    asserts signature(A) == signature(B) and reports the direction the server
    actually honours. Exit 0 = contract proven, 4 = mismatch (do NOT start a
    long run on 4), 3 = setup error.
    """
    # DB resolution BEFORE the first get_settings() call: settings are
    # lru_cached, so the resolved URL must be in the environment by then.
    try:
        activate_db(args)
    except BackfillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    cfg = get_settings()
    if not cfg.firms_map_key:
        print("ERROR: FIRMS_MAP_KEY is not set (export it or put it in backend/.env).",
              file=sys.stderr)
        return 3

    from sqlalchemy import text as sql_text

    from app.db import SessionLocal, create_all

    create_all()
    db = SessionLocal()
    db.execute(sql_text("SELECT 1"))
    from app.models import Aoi
    from app.seed.pipeline import ensure_preset_aois

    aoi = db.get(Aoi, args.aoi)
    if aoi is None:
        ensure_preset_aois(db)
        aoi = db.get(Aoi, args.aoi)
    if aoi is None:
        print(f"ERROR: AOI '{args.aoi}' does not exist and is not a preset.",
              file=sys.stderr)
        db.close()
        return 3
    bbox = f"{aoi.west:.4f},{aoi.south:.4f},{aoi.east:.4f},{aoi.north:.4f}"

    days = args.verify_days
    end = args.verify_end
    start = end - timedelta(days=days - 1)
    product = args.verify_product
    print(f"Chunk verification: {product} {start}..{end} ({days} days) "
          f"over {aoi.id} [{bbox}]")
    print("  A: one request anchored at the window start (docs direction)")
    print("  C: one request anchored at the window end   (direction probe)")
    print("  B: one 1-day request per day (ground truth)")
    print(f"  pacing: >= {args.min_interval}s between requests\n")

    def fetch_sig(c_start: date, c_end: date) -> tuple[list[tuple], int]:
        url = chunk_url(cfg, product, bbox, c_start, c_end)
        status, body = http_get(url, args.timeout, args.retries, args.min_interval)
        ensure_firms_csv(url, status, body)
        recs, _rej, total, _s = parse_and_tag(body, aoi.id, product)
        return row_signature(recs), total

    sig_a, n_a = fetch_sig(start, end)
    sig_c, n_c = fetch_sig(end, end)
    sig_b: list[tuple] = []
    n_b = 0
    day = start
    while day <= end:
        sig_1, n_1 = fetch_sig(day, day)
        sig_b.extend(sig_1)
        n_b += n_1
        day += timedelta(days=1)
    sig_b.sort()

    print(f"\nrows: A(n-day from start)={n_a}  C(n-day from end)={n_c}  "
          f"B(1-day x {days})={n_b}")
    if n_b == 0:
        print("WARN: the ground-truth window has 0 rows - pick a known-active "
              "period via --verify-end or the check proves nothing.")

    if sig_a == sig_b:
        print("direction: FORWARD (matches the official docs: "
              "[DATE .. DATE + DAY_RANGE - 1])")
    elif sig_c == sig_b:
        print("direction: BACKWARD (server disagrees with the docs - "
              "report to FIRMS support before running the backfill)")
    else:
        print("direction: UNKNOWN (neither anchor reproduces the 1-day union)")

    if sig_a != sig_b:
        set_a, set_b = set(sig_a), set(sig_b)
        print("FAIL: the n-day request does not reproduce the 1-day union. "
              "Do NOT start the long backfill. Sample differences:",
              file=sys.stderr)
        for t in list(set_a - set_b)[:3] + list(set_b - set_a)[:3]:
            print(f"  diff row: {t}", file=sys.stderr)
        db.close()
        return 4
    print(f"OK: one {days}-day request == {days} x 1-day requests "
          f"({n_b} rows, anchored {start}). The chunking contract holds.")
    db.close()
    return 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.status:
        return cmd_status(args)
    if args.build_snapshot:
        return cmd_build_snapshot(args)
    if args.verify_chunking:
        return run_verify(args)
    if args.invalidate_only:
        cfg = get_settings()
        avail = fetch_availability(cfg, args.timeout, args.retries,
                                   args.min_interval, dry_run=False)
        state = load_state(Path(args.state), args.aoi, avail, dry_run=False)
        print(f"Availability: {avail.source.upper()} - {avail.detail}")
        print(f"Invalidation: migrated={state.report['migrated']}, "
              f"SP beyond max_date={state.report['invalidated_sp_beyond_max']} "
              f"(0-row {state.report['invalidated_sp_beyond_max_zero_rows']}), "
              f"NRT before min_date={state.report['invalidated_nrt_before_min']} "
              f"(0-row {state.report['invalidated_nrt_before_min_zero_rows']})")
        save_state(state, Path(args.state), avail)
        print(f"State written to {args.state}")
        return 0
    if args.all_presets:
        # Sequential full runs per preset AOI, each with its own state file.
        exit_code = 0
        from app.seed.profiles import PRESET_PROFILES

        for profile in PRESET_PROFILES:
            args.aoi = profile.aoi_id
            args.state = str(REPO_ROOT / "data" / "firms_backfill"
                             / f"backfill_state_{profile.aoi_id}.json")
            print(f"\n{'=' * 72}\nAOI {profile.aoi_id}\n{'=' * 72}")
            code = run(args)
            exit_code = exit_code or code
        return exit_code
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
