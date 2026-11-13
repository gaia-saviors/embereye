# Architecture Decision Records

Each record: decision, alternatives rejected, rationale. Written so a
2 a.m. operator does not re-litigate settled questions.

## ADR-1 — Aggregate-level transfer functions (Tier-1) as the harmonization backbone

**Decision.** MODIS→VIIRS transfer functions fitted on monthly AOI
aggregates (Theil–Sen, log1p space), applied per day with seam weighting.
Tier-2 elements (duplicate correction, near-synchronous footprint pairing)
are implemented as diagnostics; Tier-3 episode clustering operates on the
canonical detections independently of the transfer.

**Rejected.** Pixel-level fusion (needs L2 geolocation fields FIRMS CSV
does not carry; correctness-per-hour is poor); event-level-first
harmonization (harder to validate; episode statistics still depend on the
count/FRP bridge).

**Rationale.** Defensible with published anchors (Vadrevu 2018 monthly
r²≈0.98), fully computable from FIRMS exports, and maps directly onto the
calendar's daily grain. Diagnostics keep the pixel-level evidence visible.

## ADR-2 — VIIRS-equivalent as the harmonized target unit

**Decision.** The fused series is expressed in VIIRS-equivalent units; the
MODIS-only era is transferred forward, the overlap era blends, the
VIIRS-forward era is native.

**Rejected.** MODIS-equivalent units (would require back-casting VIIRS
with higher uncertainty and fights the direction the record is heading:
S-NPP NRT ceased 2026-11-01, NOAA-20/21 carry the future).

**Rationale.** The record's future is VIIRS; the transfer only has to span
the seam. NASA's own C2 reprocessing cross-calibrates VIIRS against Aqua
MODIS as the reference — the same direction of fit.

## ADR-3 — Deterministic synthetic seed archive, generated at deploy time

**Decision.** The seeder synthesizes a calibrated active-fire archive
(fixed-seed RNG keyed by seed version + AOI) by simulating true fires and
sampling per-sensor detection physics; runs fully offline; is upgraded
with live FIRMS data when a key and network exist.

**Rejected.** Shipping static CSV snapshots (zip bloat, licensing ambiguity
of bundled "data", impossible to rescale); mocking the FIRMS API with fake
HTTP (fragile, no schema exercise); requiring a MAP_KEY at first run
(breaks the out-of-the-box contract).

**Rationale.** Zero-dependency first boot, byte-stable across rebuilds,
honest labeling everywhere, and the full pipeline (ingest → dedup →
harmonize → serve) executes exactly as it would on live data.

## ADR-4 — PostgreSQL in production, SQLite in tests/dev; portable ORM layer

**Decision.** SQLAlchemy 2 models with no vendor-specific constructs;
compose runs PostgreSQL 16 with a healthcheck; tests and `make
backend-dev` run SQLite.

**Rejected.** PostGIS (spatial predicates here are bbox prefilter + exact
point-in-polygon over ≤10⁶ rows — measured fast, no extension lock-in);
DuckDB (columnar fits artifacts, not transactional AOI/jobs state).

**Rationale.** One schema, two engines, zero divergence bugs; the
production path gets connection pooling, healthchecks and a real migration
surface.

## ADR-5 — Materialized derived data; the API never recomputes analytics

**Decision.** DailyAggregate / HarmonizedDaily / CalibrationModel /
FireEpisode / SeasonMetrics are written by the pipeline (seeder or
background AOI job); routers only read. User AOIs trigger a background
aggregation job with progress polling.

**Rejected.** On-demand recomputation per request (latency and CPU spike
per page view; anomaly windows recomputed differently between requests);
client-side aggregation (breaks the frozen contract, duplicates science in
TS).

**Rationale.** Deterministic payloads, bounded latency, one place where
the science runs. Matches the Volume 5 "build-time heavy, browser-light"
principle adapted to an operational service.

## ADR-6 — Hand-rolled SVG calendar matrix

**Decision.** The multi-year calendar is a custom SVG grid (years × DOY
slots) with anomaly rings, dashed quiet flags, season onset/end markers
and a per-day provenance strip.

**Rejected.** cal-heatmap / react-calendar-heatmap (no seam-aware cells,
no provenance texture, no season markers); ECharts calendar (same limits,
heavier).

**Rationale.** The challenge's core artifact carries semantics no library
models: era provenance per day, seam weight, dual anomaly classes, season
markers. ~300 lines of well-understood SVG beat a wrapper fighting us.

## ADR-7 — MapLibre GL JS (2D analysis mode), not Cesium

**Decision.** MapLibre with a dark raster basemap, GeoJSON hotspot layers,
heatmap layer, AOI drawing (shift-drag bbox, click-polygon), GIBS overlay.

**Rejected.** CesiumJS (the Volume 5 demo-first choice) needs ion/photoreal
tokens and network for its wow factor — an operational platform must render
fully offline; globe.gl (pretty, not GIS-grade control).

**Rationale.** The deliverable is analysis-grade 2D with guaranteed offline
behavior; hotspot layers are local vectors and render regardless of tile
reachability. GIBS/CARTO enrich when online, degrade silently when not.

## ADR-8 — The network is an enhancement, never a dependency

**Decision.** Every external call (FIRMS API, MAP_KEY probe, GIBS, basemap
tiles) is wrapped in explicit status results with bounded retries; failure
never crashes a request — the platform reports LIVE/SEED mode truthfully.

**Rejected.** Fail-fast startup without data (violates out-of-the-box);
silent fake "live" data (dishonest).

**Rationale.** Verified in this environment: FIRMS was unreachable from
the build sandbox and the platform still shipped full functionality on the
seed archive. `POST /api/live/fetch` returns OK/PARTIAL/UNREACHABLE/
SKIPPED with per-source detail instead of throwing.

## ADR-9 — Frozen API contract, additive evolution

**Decision.** `backend/app/schemas.py` and `frontend/src/api/types.ts` are
mirror definitions; response fields are additive-stable. The TS build
fails on contract drift because both sides compile against the same
shapes.

**Rejected.** OpenAPI codegen (extra toolchain for 17 routes; hand
mirroring with a test suite catches drift at CI speed); versioned URLs
(overkill pre-external-consumers).

## ADR-10 — Single science core, no duplicated logic

**Decision.** Python owns every number; the frontend renders only. No
client-side recomputation of aggregates, z-scores or ratios beyond
trivial formatting.

**Rejected.** Duplicated logic for "instant" interactions (two
implementations drift; the materialized ledger is fast enough).
