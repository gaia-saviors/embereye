# DATA.md — where EmberEye's data comes from, where it lives, how to move it

Everything in this file was verified on the real backfill (2026-11-02,
nile-delta-egypt, 171,853 hotspots rows). The honest notes are marked **HONEST**.

## 1. The one environment variable that decides where data lives

| Setting | Value |
|---|---|
| Env var (primary) | `EMBEREBEYE_DATABASE_URL` |
| Env var (alias)   | `EMBEREBEYE_DB_URL` |
| Compose value     | `postgresql+psycopg://embereye:<pw>@db:5432/embereye` (injected by docker-compose.yml) |
| App/test default  | `sqlite:///./embereye_dev.db` — **scripts never use it silently** |

- The **app** (uvicorn) still starts with the default if the env var is
  missing, and since v1.0.4 logs the resolved target at startup:
  `database: sqlite file: ./embereye_dev.db` (engine + path, never a password).
- The **scripts** (`backfill_firms.py`, `audit_real.py`,
  `sqlite_to_postgres.py`) **fail loudly with exit 2** when no env var is set.
  Explicit opt-in to a local file: `--sqlite PATH`.
- Every script prints the resolved target at startup, e.g.
  `Database: postgresql (psycopg) host localhost:5432, database embereye   [source: EMBEREBEYE_DATABASE_URL]`.
  **Always read that line before letting a run proceed.**

What happened (recorded so it never repeats): the v1.0.3 backfill ran without
the env var, silently created `./embereye_dev.db`, and the whole real archive
(171,853 rows) landed there while the compose Postgres stayed empty.

## 2. Current real-data status (Ali's machine, 2026-11-02)

- Backfill COMPLETE for all 5 satellites (state `MODIS 1900/1900`), database:
  **`embereye_dev.db` (SQLite, ~75.6 MB, repo root)**.
- hotspots 171,853 rows: Terra 6,752 (2000-11-02..2026-11-01), Aqua 6,870
  (2002-07-07..2026-10-28), SNPP 93,372 (2012-01-20..2026-10-31),
  N20 46,137 (2018-04-01..2026-10-31), N21 18,722 (2023-03-01..2026-10-31).
- Derived: harmonized_daily 9,496 · daily_aggregates 14,366 · fire_episodes 40,639.
- Check any database at any time:

  ```powershell
  python scripts\backfill_firms.py --status --sqlite .\embereye_dev.db
  ```

## 3. Running the app on the real data (the two options)

### Option (a) — point the stack at the SQLite file (zero data movement; safest first step)

```powershell
cd embereye
# optionally: $env:EMBEREBEYE_SQLITE_HOST_PATH = "C:\path\to\embereye_dev.db"
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml up --build
# calendar: http://localhost:8080
```

What the override does: mounts the SQLite file read-write into the backend
(`/data/embereye_dev.db`), sets `EMBEREBEYE_DATABASE_URL=sqlite:////data/embereye_dev.db`,
disables the seeder (a command that only prints), and drops the SQLite journal
mode to DELETE (`EMBEREBEYE_SQLITE_JOURNAL`, WAL can misbehave on Windows bind
mounts). Make sure no standalone script is writing the file while the stack runs.

### Option (b) — migrate to the compose Postgres (recommended for the final demo)

```powershell
cd embereye
docker compose up -d db
$env:EMBEREBEYE_DATABASE_URL = "postgresql+psycopg://embereye:embereye_dev_pw@localhost:5432/embereye"
python scripts\sqlite_to_postgres.py --sqlite .\embereye_dev.db
# re-run after a failed attempt:  ... --truncate-first
# then the full stack (seeder will REFUSE to seed real data and exit 0):
docker compose up --build
```

The migration verifies source/target row counts per table and exits 4 on any
mismatch (do not trust a target that failed verification).

### What `docker compose up` would do to real data (seeder guard, v1.0.4)

- There is **no `DATA_MODE=SNAPSHOT` mode** — `DATA_MODE` is `AUTO | SEED | LIVE`.
  On a healthy stack the seeder runs `python -m app.seed_run` once.
- Before the guard: on an empty Postgres the seeder would lay the synthetic
  archive (`replace_aoi_records` **wipes each preset AOI first**), and in
  AUTO/LIVE mode it would add a synthetic *underlay* next to live rows — i.e.
  real data could be replaced or mixed.
- **Guard added (v1.0.4):** if `hotspots` holds any real FIRMS rows
  (`firms_product` is `SP`/`NRT`; synthetic rows carry `""`), the seeder logs
  `REAL DATA PRESENT - seeding refused: N rows (per-AOI breakdown)` and exits
  **0** (a deliberate no-op, so `compose up` stays green). It only proceeds
  over real data when BOTH `FORCE_SEED=1` AND `EMBEREBEYE_ALLOW_SEED_OVER_REAL=1`
  are set — and even then it logs a warning. Back up first (docs/BACKUP.md).

## 4. Raw FIRMS chunk copies — **HONEST** status

- **The current real archive was fetched WITHOUT raw copies.** The v1.0.3 run
  stored only `data/firms_backfill/state.json`; no per-chunk CSVs exist for
  those 3,878 requests. The fetch is deterministic (proven: the S-NPP re-fetch
  after the `--fresh` accident reproduced the same 93,372 rows), but strictly
  speaking the raw server responses are gone.
- **From v1.0.4 on**, every fetched chunk's verbatim response body is saved
  *before parsing* to `data/firms_backfill/raw/<product>/<start>_<end>.csv`,
  idempotent on resume, and `--fresh` never deletes the raw tree. The
  availability CSV is kept under `raw/_availability/`.
- **`--build-snapshot`** exports the DB rows (all 26 canonical columns) to
  `data/firms_backfill/snapshots/hotspots_<utcstamp>.csv`:

  ```powershell
  python scripts\backfill_firms.py --build-snapshot --sqlite .\embereye_dev.db
  ```

  It reports how many raw chunk copies exist on disk. For the current data
  that number is 0 — the snapshot IS the row-level backup of the archive.

## 5. The --fresh rules (v1.0.3 footgun, fixed)

- `--fresh` deletes **only the satellites owned by `--sensors`**
  (`MODIS` → Terra+Aqua, `SNPP` → SNPP, `NOAA20` → N20, `NOAA21` → N21),
  prints the exact per-satellite delete counts, and asks for confirmation
  (`--yes` skips the prompt in scripts). Other satellites' rows are untouched,
  and only those sensors' chunk state is reset — S-NPP's 1,078 completed
  chunks survive a later `--sensors NOAA20,NOAA21 --fresh`.
- `--fresh-all-sensors` is the explicit everything-wipe for an AOI.
- Raw copies are never deleted by `--fresh`.
- The accident that forced this: `--sensors NOAA20,NOAA21 --fresh` wiped all
  93,372 S-NPP rows (refetched afterwards, same result).

## 6. Backing up (details and PowerShell-safe commands: docs/BACKUP.md)

- SQLite: stop the writers, then `Copy-Item embereye_dev.db backup\...` (+ the
  `-wal`/`-shm` siblings if present), or `VACUUM INTO` for a compact snapshot.
- Postgres: `pg_dump` with `-Fc -f` inside the container + `docker compose cp`
  (never `>` redirects from PowerShell — they write UTF-16).
