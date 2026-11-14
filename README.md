<div align="center">

<img src="frontend/public/embereye-mark.png" width="130" alt="EmberEye satellite-fire logo" />

# EmberEye

### One record. Five satellites. Twenty-six years of fire.

An Earth-observation intelligence platform that harmonizes NASA MODIS and VIIRS
active-fire detections into a single, cross-calibrated burning-activity record
for 2000–2026.

[![CI](https://github.com/3lyly0/embereye-spaceapps/actions/workflows/ci.yml/badge.svg)](https://github.com/3lyly0/embereye-spaceapps/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.162-009688.svg)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](https://www.docker.com/)
[![GHCR](https://img.shields.io/badge/container-GHCR-2496ED.svg)](https://github.com/users/3lyly0/packages/container/package/embereye-backend)

**NASA Space Apps Challenge 2026** · Cairo, Egypt · [Official challenge brief](https://www.spaceappschallenge.org/2026/challenges/modis-and-viirs-hot-spots/)<br />
**Team:** Gaia Saviors · **Challenge:** *Harmonization of MODIS and VIIRS Hot Spots*

</div>

> **Data note — not an operational alert or forecast service.** The downloadable `v1.0.8` archive contains **171,855 real NASA FIRMS detections for the Nile Delta** and deterministic synthetic seed records for the other preset AOIs. The MODIS-only pre-2012 segment is a calibrated, model-dependent transfer into VIIRS-equivalent units, not direct VIIRS observations. The app labels provenance and uncertainty; do not interpret the seed or harmonized estimate as a real-time warning.

---

## Why EmberEye exists

Between 2000 and today, NASA's active-fire monitoring moved from the **1 km
MODIS** instruments (Terra, Aqua) to the **375 m VIIRS** sensors (Suomi-NPP,
NOAA-20, NOAA-21). Each transition changes how many fires are detected:

- VIIRS's finer resolution produces an artificial **~3–5× multiplier** on
  small agricultural and understory fires.
- Going from one satellite to a constellation — and retiring Suomi-NPP in
  November 2026 — creates **step changes** that look like climate-driven
  shifts in fire regimes.
- Naively concatenating the products produces a 26-year series whose
  "trend" is mostly instrument history.

EmberEye builds the bridge: a **VIIRS-equivalent, cross-calibrated daily
record** with explicit uncertainty, provenance, and a UI built for scientific
scrutiny rather than screenshot appeal.

---

## What you can do with it

### 26-year burning-activity calendar (`/`)
- Daily (DOY 1–366) and monthly matrices spanning **2000–2026**.
- Three accessible colour ramps: **Magma**, **Viridis**, **Cividis**.
- Latest-month-vs-historical-median KPIs and record-peak highlighting.
- Era tags for **MODIS-only (2000–2011)**, **overlap (2012–2026)**, and the
  VIIRS-forward period.

### Geospatial map (`/map`)
- MapLibre GL rendering of the selected AOI's **FIRMS or clearly labelled seed
  detections**, with FRP-proportional markers and per-sensor colours. The Nile
  Delta snapshot is real FIRMS; other preset regions use synthetic demo seed.
- Temporal scrubber with playback speeds (1/3/7-day steps), pause, and seek.
- AOI engine: draw a polygon on satellite imagery or enter a lat/lon bbox;
  the backend harmonizes the selected area on demand.

### Analytics console (`/analytics`)
- Six KPI cards: harmonized detections, fire radiative energy (PJ), mean
  episode duration, peak burning index, season length, static-source share.
- Raw MODIS vs. raw VIIRS vs. harmonized series at daily, weekly, and monthly
  resolution (Apache ECharts).
- Day-of-year climatology with the **LOYO 95% prediction envelope**.
- Spatiotemporal fire-episode table with duration, peak FRP, detection count,
  and centroid.

### Streaming export (`/api/export`)
- Hotspot CSV and RFC 7946 GeoJSON use chunked database cursors and streaming
  responses, with a 500,000-detection safety cap.
- Harmonized daily, daily-aggregate, and episode exports are filterable by AOI
  and date. Hotspot responses include exact total/truncation headers.

### Six AOI presets

| Preset | Region | Fire regime |
|---|---|---|
| `nile-delta-egypt` | Nile Delta, Egypt | Agricultural residue burning + industrial flares |
| `punjab-india` | Punjab, India | Post-monsoon crop-residue burning (canonical ~4.8× bias) |
| `california-sierra` | Sierra Nevada, USA | Summer/autumn wildfires |
| `borneo-peatlands` | Central Kalimantan, Indonesia | Smouldering peat fires, ENSO-correlated |
| `pantanal-brazil` | Pantanal, Brazil | Cerrado–wetland dry-season burning |
| `mediterranean-greece` | Aegean & mainland Greece | Heatwave mega-fires |

---

## How EmberEye addresses the challenge

The challenge asks for a consistent MODIS–VIIRS burning-activity calendar and
a web app for exploring a region and time span, comparing patterns and unusual
periods, and downloading analysis. EmberEye follows that shape directly:

- It normalizes the two FIRMS product families and estimates MODIS activity in
  VIIRS-equivalent units using the measured overlap period; it does not invent
  pre-launch VIIRS observations.
- It shows the long record as daily/monthly calendars, a geospatial map, and
  time-series/season/episode analyses with range and AOI selection.
- It preserves platform, product, provisional NRT status, and uncertainty so
  sensor transitions and estimated eras remain visible to the user.
- It exports the filtered observations and derived series for downstream use.
- Deterministic synthetic rows are demo fixtures only and are never presented
  as NASA FIRMS observations.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              Browser (React 18)                              │
│   CalendarView · MapView · AnalyticsView   —   MapLibre GL · Apache ECharts   │
│   Zustand store · Tailwind "Warm Basalt" design system · accessibility ramps   │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │  /api (JSON, CSV, GeoJSON)
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                              FastAPI service                                 │
│  routers/  meta · aois · hotspots · calendar · analytics · export · live      │
│  schemas.py (Pydantic v2) · geometry.py (bbox / point-in-polygon)             │
├──────────────────────────────────────────────────────────────────────────────┤
│                          Harmonization engine                                │
│  ingest/   canonical → dedup → persist          (FIRMS CSV → canonical rows)  │
│  harmonize/ calibration → moy → climatology → episodes → series → pipeline    │
├──────────────────────────────────────────────────────────────────────────────┤
│                              Data layer                                     │
│  SQLAlchemy 2.0 · PostgreSQL 16 (compose) or SQLite (dev / offline archive)  │
│  composite indexes on (aoi_id, local_date, satellite)                        │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │  optional MAP_KEY
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                     NASA FIRMS API  (MODIS + VIIRS)                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Stack:** FastAPI · Python 3.11 · SQLAlchemy 2.0 · Pydantic v2 · NumPy · SciPy ·
React 18 · TypeScript 5.6 · Vite 8 · Tailwind CSS · Zustand · MapLibre GL ·
Apache ECharts · PostgreSQL 16 / SQLite · Docker Compose · GitHub Actions.

---

## Methodology

The full auditable specification — inputs, deterministic procedure, literature
anchor, and where each number surfaces — is in
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md). Decisions and rejected
alternatives are recorded in [`docs/ADR.md`](docs/ADR.md).

### 1. Canonical normalization
Both FIRMS export families are mapped to one schema: sensor family, satellite,
acquisition UTC, local solar day, brightness temperatures, FRP, confidence
class, and pixel area. Saturation flags and negative sentinels are preserved,
never silently clamped. Rejections are counted with samples.

### 2. Duplicate suppression
Same-satellite detections within one overpass minute and ≤0.8 km (MODIS) or
≤0.2 km (VIIRS) are clustered; the highest-confidence record represents the
cluster and carries the cluster-mean FRP. This adapts Zheng et al. (2026) to
FIRMS's minute-level timestamps without treating adjacent 375 m pixels as
duplicates.

### 3. Comparable-subset transfer (the heart of the bridge)
The MODIS→VIIRS transfer is calibrated **only on daytime, non-static
detections** — mixing populations is what produced the drifting 12–40× ratios
seen in earlier audits. EmberEye fits **month-of-year transfer factors** over
2012–2022 (MODIS overpasses are stable until 2022; Terra/Aqua drift begins in
2023), with circular smoothing and a minimum-count guard that falls back to
the annual ratio for sparse months.

Validation is **leave-one-year-out** (LOYO):

| Metric | Result |
|---|---|
| Annual LOYO MAPE (pooled, 11 folds) | **7.03%** |
| Annual mean bias | **−2.01%** |
| Annual empirical 95% prediction interval | **[−18.15%, +12.21%]** |
| Per-platform annual LOYO MAPE | **9.25%** |
| Calendar-month / ISO-week LOYO MAPE | **33.77% / 83.52%** |

These are results for the v1.0.8 offline-release snapshot and the Nile Delta
AOI, not a universal accuracy guarantee. Monthly and weekly intervals are
much wider than the annual interval; do not quote the annual MAPE as expected
error for an individual week. The 2026-11-02 audit reported different annual
figures (7.5%, +1.6%, PI [−14.3%, +17.8%]); both snapshots are documented in
[`docs/AUDIT_REAL.md`](docs/AUDIT_REAL.md), and the discrepancy is not hidden.
Aggregation-matched LOYO intervals are carried into
`harmonized_daily.n_harmonized_lo/hi` and rendered as a band in the UI. A
Theil–Sen power-law model is retained as a comparison column; it was retired
because its LOYO bias was −15% to −23%.

### 4. Intra-VIIRS constellation normalization
NOAA-20 (2018) and NOAA-21 (2023) are cross-checks, **never summed** with
Suomi-NPP (pairwise r ≈ 0.991). Daily overpass effort is normalized so the
constellation expansion does not create a fake upward step, and the
post-Suomi-NPP era continues on NOAA-20 divided by day/night continuity
factors measured over 103 shared months.

### 5. Climatology and episodes
A day-of-year baseline is built on the fixed 2012–2019 reference period with a
±15-day moving window. LOYO empirical 95th-percentile scoring flags extreme
anomalies. KD-tree clustering (≤5 km, ≤2 days) groups detections into fire
episodes, from which duration, peak FRP, and fire radiative energy (PJ) are
derived. Static industrial sources (FIRMS `type=2` primary, persistence mask
secondary) are tagged and displayed separately, never silently deleted.

### Honest limitations
The pre-2012 segment is **model-dependent**: the transfer is assumed stationary
back to 2000 because no VIIRS observation exists before 2012. MCD64A1 burned
area can corroborate seasonality but is not ingested. These limitations are
also exposed at runtime through `GET /api/analytics/methods`; see
[`docs/AUDIT_REAL.md`](docs/AUDIT_REAL.md) for the real-data audit. EmberEye is
not a fire-spread model, operational early-warning system, or substitute for
official incident information. NASA FIRMS NRT observations are provisional and
may be revised when standard products become available.

---

## Space agency data used

Real satellite observations ingested by EmberEye come from **NASA FIRMS**.
Offline/demo mode can also generate clearly labelled synthetic records; those
are not NASA observations. This release does not ingest ESA, JAXA, or commercial
imagery. Full provenance, terms, and required citations live in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

| Source | Product | Platforms | Resolution |
|---|---|---|---|
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov) | MODIS Collection 6.1 active fire (`MODIS_SP` / `_NRT`) | Terra, Aqua | 1 km |
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov) | VIIRS 375 m active fire (`VIIRS_SNPP_SP`; NOAA-20/21 `SP` and `NRT`) | Suomi-NPP (historical), NOAA-20, NOAA-21 | 375 m |

NASA states that new Suomi-NPP data delivery ceased at 13:00 UTC on 2 November
2026. Historical S-NPP products remain available; the live NRT path uses
NOAA-20 and NOAA-21 instead. See [NASA's FIRMS transition note](https://www.earthdata.nasa.gov/news/blog/transitioning-from-suomi-npp-viirs-omps-what-firms-users-need-know).

**Required citations**

1. Giglio, L., Schroeder, W., & Justice, C. O. (2016). *The Collection 6 MODIS
   active fire detection algorithm and fire products.* Remote Sensing of
   Environment, 178, 31–43. https://doi.org/10.1016/j.rse.2016.02.054
2. Schroeder, W., Oliva, P., Giglio, L., & Csiszar, I. A. (2014). *The New
   VIIRS 375 m Active Fire Detection Data Product.* Remote Sensing of
   Environment, 143, 85–96. https://doi.org/10.1016/j.rse.2013.12.008

### Real data vs. offline seed — always labelled

EmberEye can run entirely offline on a **deterministic, calibrated seed
archive** that exercises the full ingest → dedup → harmonize → serve pipeline.
Synthetic rows carry an empty `firms_product`; real rows carry `SP`/`NRT`.
`GET /api/meta` reports `FIRMS_ARCHIVE`, `SYNTHETIC_SEED`, `MIXED`, or `EMPTY`,
and the UI shows the same provenance. The v1.0.8 offline release is mixed: the
Nile Delta is real FIRMS and the other preset AOIs are synthetic. Raw agency
archives are **not committed**; regenerate them with `scripts/backfill_firms.py`
and your own free `FIRMS_MAP_KEY`. `SEED` is synthetic; `LIVE` fails loudly if
FIRMS cannot be reached instead of silently substituting demo data.

---

## Repository layout

```
embereye/
├── backend/                 # FastAPI service
│   ├── app/
│   │   ├── routers/         #   API endpoints (meta, aois, hotspots, calendar,
│   │   │                    #   analytics, export, live)
│   │   ├── ingest/          #   FIRMS client, canonicalization, dedup, persistence
│   │   ├── harmonize/       #   transfer calibration, climatology, episodes, series
│   │   └── seed/            #   deterministic offline archive generator
│   └── Dockerfile · requirements.txt · pytest.ini
├── frontend/                # React + TypeScript SPA
│   ├── src/views/           #   Calendar · Map · Analytics
│   ├── src/components/      #   control ribbon, scrubber, methods modal, top bar
│   ├── src/{api,lib}/       #   typed client + colour/format helpers
│   └── e2e/                 #   Playwright performance probes
├── scripts/                 # backfill, audit, LOYO evaluation, DB migration
├── tests/                   # pytest suite (pipeline, phase 2, v1.06/v1.07)
├── docs/                    # methodology, ADRs, data provenance, audit, backup
├── frontend/public/         # shared app mark and favicon assets
├── .github/workflows/       # CI tests/build · tag-triggered GHCR publish
├── docker-compose.yml       # PostgreSQL + seeder + backend + nginx SPA
├── docker-compose.public.yml       # public read-only PostgreSQL profile
├── docker-compose.sqlite.yml       # local read-write host-SQLite profile
├── docker-compose.sqlite.public.yml # public read-only SQLite profile
├── Makefile · .env.example · LICENSE · CITATION.cff
```

The `backend/` + `frontend/` split is deliberate: it matches the container
boundaries, keeps `backend/` importable as a Python package (`pythonpath =
backend`), and avoids a root-level `src/` refactor that would break the
Dockerfiles, CI caches, and relative imports.

---

## Local setup

### Prerequisites
Docker Desktop (recommended), or Python **3.11** + Node **22.12+** for a local run.
No API key is required: the stack starts on the labelled seed archive.
The template defaults are for local/demo use; do not expose them unchanged on a public host.

### Quick start — Pull published GHCR images

The release workflow publishes multi-architecture backend and frontend images
to GHCR on stable version tags. During preparation, the repository and packages
may remain private; users then need repository access and a classic GitHub PAT
with `read:packages`. For public submission, make the repository public and set
**both GHCR packages to Public separately**: GHCR package visibility does not
automatically become public when the linked repository does. Public container
packages can then be pulled without login ([GitHub docs](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)).
Run the commands below from the root of a clone of this repo.

```bash
git clone https://github.com/3lyly0/embereye-spaceapps.git
cd embereye-spaceapps
cp .env.example .env
# Optional: pin an exact release; omit to use the latest published tag.
echo 'EMBEREYE_IMAGE_TAG=v1.1.0' >> .env
# While the packages are private, log in with a classic PAT (read:packages).
# Omit this command after both GHCR packages have been made Public.
docker login ghcr.io -u 3lyly0
docker compose pull
docker compose up -d --no-build

# Web app:      http://localhost:8080
# API docs:     http://localhost:8000/docs
# Health:       http://localhost:8000/api/health
```

With no `FIRMS_MAP_KEY`, the one-shot seeder creates the labelled deterministic
synthetic demo archive. The UI marks it as synthetic; it is not NASA observations.
To run the Nile Delta real-data snapshot instead, use the SQLite archive option
below with the same prebuilt images.

For a public Linux host with loopback-bound service ports, `PUBLIC_READ_ONLY=true`,
API rate limiting, and a Caddy HTTPS reverse proxy, see
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). The application has no user accounts;
keep public deployments view-only unless authentication is added.

### Option A — Build the full stack from source

```bash
git clone https://github.com/3lyly0/embereye-spaceapps.git
cd embereye-spaceapps

cp .env.example .env          # optional; every value has a default
docker compose up --build

# Web app:      http://localhost:8080
# API docs:     http://localhost:8000/docs
# Health:       http://localhost:8000/api/health
```

The `seeder` service ingests data and runs the full harmonization pipeline
once, then exits. Data persists in the `pgdata` volume.

### Option B — Serve an existing SQLite archive

**Download the v1.0.8 ready-to-run archive (no API key needed):**

```bash
# Download the pre-processed ~383MB database to run locally without an API key
curl -L -o embereye_dev.db "https://github.com/3lyly0/embereye-spaceapps/releases/download/v1.0.8-offline-data/embereye_dev.db"
```

> While the repository is private, release assets require an authenticated
> GitHub session. If `curl` returns 404, run `gh auth login` and use
> `gh release download v1.0.8-offline-data -R 3lyly0/embereye-spaceapps` instead.
> Once the repository is public, the direct `curl` command works anonymously.

This snapshot contains real NASA FIRMS observations for the Nile Delta AOI and
deterministic synthetic demo records for the other preset AOIs. The v1.0.8
derived tables were rebuilt from the stored hotspots; see
[`docs/AUDIT_REAL.md`](docs/AUDIT_REAL.md) for the snapshot-specific metrics.


If you already ran `scripts/backfill_firms.py` and have `embereye_dev.db`, use
the GHCR images with `--no-build`:

```bash
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml pull
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml up -d --no-build
```

This mounts the file read-write, disables the seeder, and switches SQLite to
`DELETE` journal mode (safer on Windows bind mounts). See
[`docs/DATA.md`](docs/DATA.md) for the Postgres migration path.

### Option C — Local development (no Docker)

```bash
# Backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt

cd backend
python -m app.seed_run
uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
cd frontend
npm ci
npm run dev                          # http://localhost:5173, proxies /api
```

The Makefile wraps the common tasks:

```bash
make up          # docker compose up --build -d
make logs        # tail service logs
make test        # run the backend/pipeline test suite
make seed        # re-run the seeding + harmonization job
```

### Tests

```bash
python -m pytest -q                  # backend + harmonization suite
cd frontend && npx tsc --noEmit      # TypeScript check
cd frontend && npm run build         # production bundle
```

CI runs the same three gates plus a Docker Compose smoke test on every push.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `EMBEREBEYE_DATABASE_URL` | `sqlite:///./embereye_dev.db` | Database target (Postgres URL or SQLite path) |
| `DATA_MODE` | `AUTO` | `AUTO` \| `SEED` \| `LIVE` |
| `FIRMS_MAP_KEY` | empty | Free NASA FIRMS key; enables live ingestion |
| `SEED_START` / `SEED_END` | `2000-11-02` / `2026-11-01` | Seed archive window |
| `BASELINE_START` / `_END` | `2012` / `2019` | LOYO climatology reference period |
| `CALIBRATION_START` / `_END` | `2012` / `2022` | MODIS→VIIRS transfer window |
| `BACKEND_PORT` / `FRONTEND_PORT` | `8000` / `8080` | Published ports |

See [`.env.example`](.env.example) for the complete annotated list.

---

## API surface

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/meta` | Platform metadata, provenance, citations |
| `GET` | `/api/aois` | Preset and custom areas of interest |
| `POST` | `/api/aois` | Create an AOI and trigger harmonization |
| `GET` | `/api/calendar` | Calendar matrix (daily / monthly) |
| `GET` | `/api/hotspots` | Filtered detections (`bbox`, date, satellite, FRP) |
| `GET` | `/api/hotspots/grid` | Aggregated map grid |
| `GET` | `/api/analytics/summary` | Headline KPIs |
| `GET` | `/api/analytics/timeseries` | Raw vs. harmonized series |
| `GET` | `/api/analytics/climatology` | DOY climatology + LOYO envelope |
| `GET` | `/api/analytics/episodes` | Fire-episode clusters |
| `GET` | `/api/analytics/methods` | Methodology, limitations, citations |
| `GET` | `/api/export` | Streaming CSV / GeoJSON export |
| `GET` | `/api/live/status` | FIRMS key/network status |
| `POST` | `/api/live/fetch` | On-demand live ingestion |

Interactive OpenAPI documentation is served at `/docs`.

---

## Team & acknowledgements

Built by **Team Gaia Saviors** for the **2026 NASA Space Apps Challenge**
(Cairo). Thanks to NASA ESDIS/LANCE for FIRMS and to the open-source
maintainers of FastAPI, React, MapLibre, ECharts, NumPy, SciPy, and SQLAlchemy.

Methodological anchors beyond the FIRMS product papers include Zheng et al.
(2026), Vadrevu et al. (2018), Li et al. (2018), Wooster et al. (2003),
Su & Yu (2026), and Jolly et al. (2015) — the full registry is in
`backend/app/harmonize/pipeline.py` and exposed at `/api/analytics/methods`.

**AI use disclosure:** AI-assisted tools contributed to implementation, debugging,
tests, documentation, and the EmberEye logo concept. The team remains responsible
for reviewing the generated changes, scientific assumptions, data provenance,
and final interpretation. AI is not used to invent or relabel observations as
NASA measurements.

---

## License & citation

Released under the [MIT License](LICENSE). The license covers the software,
not the upstream NASA observations — retain the citations in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) when redistributing derived
data.

If you use EmberEye, please cite the repository using
[`CITATION.cff`](CITATION.cff):

```bibtex
@software{embereye2026,
  author  = {{Team Gaia Saviors}},
  title   = {EmberEye: Harmonization of MODIS and VIIRS Hot Spots},
  year    = {2026},
  version = {1.1.0},
  license = {MIT},
  url     = {https://github.com/3lyly0/embereye-spaceapps}
}
```
