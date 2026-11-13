# FIRMS backfill plan — Gate 0 scope update (v1.0.3, 2026-11-02)

> **STATUS UPDATE (v1.0.4, 2026-11-02): the backfill is COMPLETE** — all five
> satellites, 171,853 real rows in Ali's `embereye_dev.db` (state MODIS
> 1900/1900). The run used this plan's §8 commands; what changed since:
> scripts now fail loudly without `EMBEREBEYE_DATABASE_URL` (or `--sqlite`),
> `--fresh` is scoped to `--sensors` with confirmation, raw chunk copies +
> `--build-snapshot` + `--status` exist, and the seeder refuses to run over
> real rows — see `docs/DATA.md`, `docs/BACKUP.md` and the v1.0.4 README
> changelog. The §8 command block below is historical.

Status: awaiting Ali's local verification run. Supersedes the v1.0.2 plan
after (a) the FIRMS area-API contract check against the official docs and
(b) the official-challenge scope review. Phase 2 is NOT started here — this
document only fixes the backfill plan and records the Gate 0 scope.

## 1. Area-API contract (verified against the official docs, 2026-11-01)

- `DAY_RANGE` is **1..5** — five days is the hard cap. The v1.0.2 default of
  10 was wrong (v1 used 5; the v1.0.2 dry-run's 536 MODIS requests came from
  a bad 10-day default) and `--chunk-days > 5` is now rejected by the script.
- `/api/area/csv/KEY/SOURCE/BBOX/DAY_RANGE/DATE` returns
  **`[DATE .. DATE + DAY_RANGE - 1]`** — forward from DATE. Chunks are
  anchored at their start day (`DATE = c_start`); the previous end-day
  anchoring would have fetched the days *after* each chunk. Both properties
  are proven on live data before a long run:

  ```powershell
  python scripts/backfill_firms.py --verify-chunking
  # A: one 5-day request anchored at the window start
  # B: five 1-day requests (ground truth)  ->  signature(A) must equal union(B)
  # C: one 5-day request anchored at the window end (direction probe)
  ```

  Exit 0 = contract proven (FORWARD); exit 4 = mismatch — do not start the
  long run. Known-active default window: VIIRS_SNPP_SP 2025-10-16..20 over
  the AOI (override with `--verify-end` / `--verify-product` / `--verify-days`).
- Runtime sanity on every chunk: rows whose `acq_date` falls outside the
  requested window are counted and abort the run past 50% of the chunk; a
  row-span shorter than the window that touches the window end prints a
  truncation WARN (episodic fires make this a hint, not an error).

## 2. Coverage: early 2000s to present (challenge requirement)

- Default window start is now **2000-11-01** = MODIS_SP min_date (Terra).
  Aqua rows appear from 2002-07 and stay split via the persisted `satellite`
  column (Terra | Aqua | SNPP | N20 | N21).
- VIIRS windows are unchanged and still come from data_availability
  (S-NPP 2012-01-20, NOAA-20 2018-04-01, NOAA-21 2023-03-01); the SP/NRT
  seam per sensor remains `SP max_date + 1`.
- Corrected request estimate (5-day chunks, window end 2026-10-31):
  **3878 requests** — see the v1.0.3 dry-run in the delivery message.

## 3. Harmonization seams (Phase 2 scope; inputs preserved now)

Seams to calibrate: Terra-only era (2000-11 .. 2002-07), Terra+Aqua
(2002-07 .. 2012-01), MODIS→VIIRS (2012-01), VIIRS constellation
(S-NPP/N20 from 2018, N21 from 2023). Bridge strategy per the challenge
goal "the sensor of origin no longer matters": **Aqua↔VIIRS is the clean
bridge** (afternoon overpass parity); **Terra is calibrated separately**
(morning overpass, different diurnal sampling).

The backfill already preserves what the seam validation needs:

- deduplication buckets by `(satellite, sensor, minute, cell)` — overlap
  rows from different satellites are never merged away;
- every row keeps `satellite`, `firms_product` (SP/NRT), brightness T4/T11,
  FRP and confidence, so per-seam bias/uncertainty can be computed from the
  real overlap data later.

Deliverable in Phase 2: per-seam bias and uncertainty report computed from
the overlap rows, feeding the calibration weights (METHODOLOGY.md §seam).

## 4. Headline analytic (Phase 2 scope)

"Current month vs historical median" for the selected AOI, plus the
highest-activity months/years ranking. The backfill's 2000+ coverage is the
input the monthly climatology needs; no code change in this phase.

## 5. User flow (exists; verified in v1.0.x)

Select an AOI (lat/lon bbox input — map drawing stays optional) and a date
range → download analytics and a CSV of the hotspots in that selection:
`/api/aois` (bbox), `/api/export?start=&end=` (CSV/GeoJSON with
`satellite`, `static_source`, provenance columns), analytics endpoints.
Remaining Phase 2 work is UX packaging, not plumbing.

## 6. Static/industrial sources count as burning activity (challenge text)

The challenge text explicitly names industrial processes and gas flares as
burning activity, so they must be **included by default**, classified, and
excludable:

- Current state (verified): rows are *flagged* (`static_source`), never
  deleted; hotspots and exports include them with the flag; FIRMS `type`
  (0/1/2) is stored for SP rows and location-matched for NRT rows. Only
  **episode clustering** excludes them today (`pipeline.py`:
  `fire_arr = arr.subset(~static_source)` — an episode is a spreading fire
  event, which a persistent flare is not).
- Phase 2: show the static-source share in the headline analytics and offer
  an opt-in toggle to exclude them; keep include-by-default everywhere.

## 7. Audience & accessibility (Phase 2 scope)

Audience: emergency responders, scientists, land managers, firefighters.
Phase 2 reviews all color ramps (calendar, map, charts) for colorblind
safety and legibility under stress; no change in this phase.

## 8. Run plan (Ali, standalone Windows PowerShell)

```powershell
# one-time setup (repo root; standalone Python 3.12+, no docker exec)
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
$env:EMBEREBEYE_DATABASE_URL = "postgresql+psycopg://embereye:<pw>@localhost:5432/embereye"
# FIRMS_MAP_KEY comes from the repo .env (loaded by the script itself)

# verify, then run (order: SNPP -> NOAA20 -> NOAA21 -> MODIS)
python scripts\backfill_firms.py --dry-run
python scripts\backfill_firms.py --verify-chunking
python scripts\backfill_firms.py --fresh                # full run
# split runs are supported (--sensors controls order and subset):
python scripts\backfill_firms.py --sensors SNPP --fresh
python scripts\backfill_firms.py --sensors NOAA20,NOAA21
python scripts\backfill_firms.py --sensors MODIS
```

- Database: `docker compose up -d db` first (the `db` service now publishes
  5432); the standalone run writes to the same Postgres volume the stack
  uses, so the app sees the backfilled data immediately.
- Output dir: `data\firms_backfill\` in the repo on the host (state files
  per AOI) — plain host files, they persist; the backfilled *data* lives in
  Postgres (named volume `pgdata`).
- The script loads the repo `.env` itself (no dependence on the shell
  profile), prints per-sensor rows + ETA per chunk with `flush=True`
  (safe to redirect with `Tee-Object`), and resumes from
  `data\firms_backfill\state.json` after any interruption.
- `--fresh` is recommended for the first real run: any rows left by v1 lack
  the `firms_product`/`firms_type` provenance columns and v1's URL
  anchoring predates the contract verification, so its partial state is not
  trustworthy even where chunks were in-coverage.
- Rough duration: 3878 requests ≈ 65 min of pacing at 1 s plus transfer —
  budget ~2-3 h total; raise `--min-interval` if FIRMS rate-limits.
