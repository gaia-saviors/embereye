# Methodology — the EmberEye harmonization bridge

This document is the auditable spec of every number the platform computes.
Each section states the input, the deterministic procedure, the literature
anchor, and where the result surfaces in the API/UI.

## 1. Canonical record model

Both FIRMS export families are normalized to one record:

| Field | MODIS C6.1 | VIIRS 375 m | Rule |
|---|---|---|---|
| `sensor_family` | MODIS | VIIRS | from header |
| `satellite` | Terra, Aqua | SNPP, N20, N21 | alias map (`T`→Terra, `NOAA-20`→N20, …) |
| `resolution_m` | 1000 | 375 | fixed per family |
| `acq_dt_utc` | acq_date + acq_time (HHMM) | same | second-level precision does not exist in FIRMS CSV; never fabricated |
| `local_date` | derived | derived | `acq_dt + round(lon/15) h` — AOI-solar calendar day (documented approximation; overpasses bracket local noon/midnight) |
| `brightness_t4_k` | `brightness` (band 21/22) | `bright_ti4` (I4) | saturation flags: I4 ≥ 367 K (Schroeder 2014); band 22 ≥ 331 K → `band21_fallback` (Giglio 2016) |
| `brightness_t11_k` | `bright_t31` | `bright_ti5` | — |
| `frp_mw` | `frp` | `frp` | negative sentinels → missing, never zero-clamped |
| `confidence_class` | 0–100 → LOW<30, NOMINAL 30–79, HIGH≥80 | l/n/h → LOW/NOMINAL/HIGH | ordinal scale shared; MODIS raw value preserved, VIIRS never receives a fabricated number |
| `pixel_area_km2` | scan × track (1→9.6 km²) | 0.25 | drives footprint matching and area metrics |

Rejections are counted with samples; nothing is silently dropped.

## 2. Duplicate correction (per sensor)

Zheng et al. (2026) rule: same-sensor detections with |Δt| < 8 s and
footprint overlap > 30% are duplicates; keep the highest-confidence record,
assign the cluster-mean FRP. Adaptation to FIRMS minute granularity:
same satellite + same overpass minute + single-link clustering at
≤0.8 km (MODIS) / ≤0.2 km (VIIRS) centre distance. The wider threshold is
per sensor by design — adjacent 375 m pixels of one fire are *not*
duplicates. Determinism: clusters are formed from lexicographically sorted
buckets; representative selection and FRP mean are pure functions of the
cluster.

## 3. Near-synchronous footprint pairing (diagnostic)

For every MODIS detection, VIIRS detections whose centres fall inside the
MODIS equivalent-area footprint (radius = √(area/π), clamped 0.56–1.75 km)
and |Δt| ≤ 5 min are aggregated (Zheng et al. 2026 use the same window;
Aqua ↔ S-NPP share the 13:30/01:30 overpass family). Outputs: matched
footprint count, contained-VIIRS count, median Δt, per-footprint
VIIRS-FRP-sum distribution. Surfaces in `/api/analytics/methods`
(`pairing_stats`). This is evidence for the transfer, not the transfer
itself.

## 4. Transfer functions (Phase 2: month-of-year factors on the comparable subset)

**Population rule (Phase 2, item 1).** The transfer is calibrated and applied
on the **COMPARABLE SUBSET: daytime, non-static detections** only. The
all-detections ratio is not a stable quantity: S-NPP night rows are ~71%
static sources that MODIS barely sees, and mixing the populations produced
the drifting 12-40x ratios of the Phase 1 audit. On the comparable subset
the annual pooled ratio is stable (7.4 / 7.2 / 7.4 over 2012-17 / 2012-21 /
2022-25, yearly range 5.8-9.5, CV ~7-10% on the Nile Delta record).

- **Model**: month-of-year factors `f[m] = VIIRS_equiv(MODIS month m)`.
  Raw factor per month-of-year = ratio of totals over the calibration
  window; circular `[1,2,1]/4` smoothing; **min-count guard**: months whose
  pooled MODIS calibration total is below 100 detections fall back to the
  annual ratio (a month with a handful of detections has no stable ratio).
- **Calibration window 2012-2022**: MODIS overpass times are stable until
  2022 (drift starts 2023: Terra 8.8 -> 7.1 UTC, Aqua 11.2 -> 13.1 UTC);
  both platforms are compared same-era.
- **Validation: leave-one-year-out** over the calibration window. For each
  held-out year the factors are refitted without it and the year's total is
  predicted; reported: MAPE, mean bias, and the empirical **95% prediction
  interval** of the relative errors. On the v1.0.8 offline-release snapshot
  (Nile Delta; 171,855 real FIRMS rows; static sources reclassified), the
  reproducible annual pooled LOYO is MAPE 7.03%, bias -2.01%, PI
  [-18.15%, +12.21%]; the per-platform variant is 9.25% / +6.41% / PI
  [-9.48%, +21.95%]. The monthly and weekly results are much less precise:
  MAPE 33.77% and 83.52%, respectively, with correspondingly wide intervals.
  These are snapshot- and aggregation-specific diagnostics, not a guarantee
  of error on an individual month or week. The 2026-11-02 audit reported
  different figures (7.5%, +1.6%, PI [-14.3%, +17.8%]); preserve that as a
  historical result and do not mix it with the v1.0.8 snapshot until the
  difference is reconciled (see `docs/AUDIT_REAL.md`).
- **Pooled vs per-platform**: both variants are fitted and validated; the
  per-platform variant wins only when its LOYO MAPE is lower by more than
  1 percentage point (materiality margin). On the v1.0.8 snapshot pooled
  MAPE is 7.03% vs per-platform 9.25%, so **pooled is adopted**; the
  Terra-only era 2000-11..2002-06 always uses the Terra-specific factor
  (the pooled factor calibrates the two-satellite mix).
- **Resolution (item 2)**: factors are constant within a calendar month, so
  daily/weekly/monthly aggregates are consistent by construction. The
  canonical view is weekly/monthly (pre-2012 MODIS daily counts are 0-3;
  scaling them individually is mostly noise); the daily view remains
  available and carries the same interval, never a falsely precise number.
- **FRP transfer**: identical machinery on comparable-subset FRP sums
  (ratio ~5, different units - fitted separately).
- **Retired (kept as comparison)**: the Phase 1 Theil-Sen power law on
  log1p monthly sums (`theil_sen_log1p`). LOYO bias -15% to -23%: it
  regressed the seasonal shape away. It stays in the Methods panel and in
  `calibration_models` as a comparison column only.
- **Legacy fallback**: records without a calibratable VIIRS overlap get
  `passthrough_fallback` (raw comparable counts, flagged, never silent).

Cross-check: Vadrevu et al. (2018) report monthly-aggregate r² ≈ 0.98 for
Aqua vs S-NPP in Punjab; the day/non-static monthly agreement on the real
Nile Delta record is Spearman ρ ≈ 0.79 over 2012-2022 (stored per AOI as
`agreement_stat`).

## 5. Harmonized daily series (Phase 2: era-clean, reference platform)

Per AOI-day, **comparable-subset** units (day, non-static,
S-NPP-equivalent):

```
MODIS_TRANSFERRED  n_h = f[moy] x comparable MODIS        [< VIIRS start]
                   (Terra-only days: Terra factor; interval = LOYO PI)
VIIRS_NATIVE       n_h = comparable reference VIIRS       [>= VIIRS start]
                   (S-NPP to 2026-11-02; then NOAA-20 / continuity factor)
NO_DATA            outside the record span
```

- **Reference-platform policy (Phase 2, items 1+5)**: from the VIIRS start
  the series is the S-NPP comparable count itself. N20/N21 observe the
  same fires (overlap-era Pearson r = 0.99) and are **never summed**. After
  S-NPP end of delivery (2026-11-02) the series continues on NOAA-20
  divided by day/night-specific continuity factors fitted on the 103
  shared months. **Direction (item 5, fixed):** `factor = successor/reference`
  over shared months; factor > 1 means the successor counts HIGHER, so the
  reference-equivalent value is **successor / factor** (the audit text
  originally had the direction backwards). On the v1.0.8 Nile snapshot,
  NOAA-20/S-NPP has 103 shared months: day factor 1.0204 (bounds 0.9829–1.1099)
  and night factor 1.0527 (0.8669–1.1695). NOAA-21/S-NPP has 44 shared months:
  day 0.9579 (0.8659–1.0338) and night 1.0285 (0.9947–1.0590). Bounds are
  calculated separately by day/night from yearly ratios over the aligned
  shared months. Historical values from the 2026-11-02 audit are retained in
  `docs/AUDIT_REAL.md` §1; do not mix them with this release snapshot. N21's
  factor is stored for the day NOAA-20 eventually ends.
- **Interval columns (item 1)**: `n_harmonized_lo/hi` carry the LOYO 95%
  prediction interval in the transferred era and the continuity-factor
  spread after the reference end; `lo = hi = n_h` in the native era (no
  transfer uncertainty). Aggregating lo/hi by summation is exact because
  the factor is constant within a month.
- **Separate layers (item 3)**: from the VIIRS start, `n_static`
  (static-source detections) and `n_night` (non-static night detections)
  of the reference platform are stored as separate layers - no MODIS
  equivalent exists before 2012, so including them earlier would fake a
  step change. They are burning activity per the challenge text: always
  shown, in totals only where the layer toggle is switched on, and the
  **static share** is displayed everywhere (`static_share` column + UI).
- The retired cosine seam weight survives only as the 0/1 era indicator.
- **Agreement score (the lie detector)**: Spearman ρ between comparable
  MODIS and S-NPP monthly sums over the calibration window, stored on the
  COUNTS calibration row.

## 6. Climatology and anomalies (Phase 2 rebuild)

- Slots: calendar-aligned 1..366 (phantom Feb 29 in non-leap years; Feb 29
  → 60, Mar 1 → 61 in every year) so ±15-day windows never drift across
  leap boundaries.
- Baseline: fixed `BASELINE_START..BASELINE_END` (default 2012-2019) of
  the comparable-subset series.
- **Anomaly rebuild (item 6)**: `flag_high` = the day's value is at or
  above the **leave-one-year-out EMPIRICAL percentile 95** of its DOY-ring
  sample (the day never competes against a distribution containing its own
  year); `flag_low` = "unusually quiet": zero detections where the LOYO
  baseline median expects activity (median > 1), surfaced visibly in UI
  and exports. **Min-count guard**: a day is only flaggable when ≥ 5
  DISTINCT baseline years contribute to its sample - otherwise `pct` is
  null and no flag is emitted (never flag on thin evidence). The old
  parametric `norm.cdf` percentile mapping and the z-based quiet flag are
  retired; z is still reported as a descriptive statistic of the LOYO
  sample.

## 7. Fire seasons (with censoring)

Per year, on the comparable-subset series: 7-day trailing rolling sum;
threshold = 10% of that year's rolling peak; onset = first day above,
end = last day above; peak day = argmax; active days = days with
n_h > 0; max streak = longest run of active days. (R3 §8.1 recipe.)
**Season censoring (item 6)**: years whose record does not span the full
 calendar (2000 starts in Nov, 2026 partial) are marked `is_partial`,
excluded from baseline statistics and marked with `*` in the UI - never
silently compared against full years.

## 8. Fire episodes

Day-pair KD-tree linking + union-find: detections of day t link to days
t−1, t−2 within 5 km (configurable). Union-find over a fixed edge set is
order-independent → the clustering is a pure function of the input (tested
under permutation). Episode stats: span, detections, peak/total FRP,
intensity-duration index `Σ FRP × duration`, centroid, bbox, sensor mix.
Top-10 by intensity index are marked `is_critical` ("critical periods").
Static-source rows are excluded from episode formation.

## 9. Static-source classification (Phase 2: type=2 primary)

**PRIMARY (item 4): FIRMS `type=2`** — SP rows carry the type column and
are flagged directly at parse time; NRT rows have no type column and
inherit the flag via the rounded (2-decimal, ≈1.1 km) location match to
SP type=2 cells. The list is rebuilt from the database on resumed runs.

**SECONDARY: the persistence mask is a tag, not a class.** A 0.05° cell
with detections on ≥60 distinct days and a hot (>320 K), low-variance
(<25 K²) per-sensor daytime T4 series sets `recurring_source` ("recurring
source") and no longer overwrites `static_source`. Whether the
persistence-only rows are flares (spread over the year) or recurring
crop-residue burning (concentrated in the harvest window) is settled by
the audit cross-tab (persistence-only × crop-like × month-of-year,
`scripts/audit_real.py static`), with a type=2-only sensitivity ratio
alongside.

Applied as flags - never deletions - and industrial sources **count as
burning activity** per the challenge text: they stay in totals, the
static share is displayed, and the layers toggle includes/excludes them
explicitly.

Reclassify an existing database once after upgrading:
`python scripts/reclassify_static.py` (then rebuild the derived tables).

## 10. Known limits (stated, not hidden)

- **Terra/Aqua orbit drift from 2023** (item 8): mean overpass hours move
  (Terra 8.8 -> 7.1 UTC, Aqua 11.2 -> 13.1 UTC over 2022-2026) with the
  day share falling; MODIS after 2022 is therefore excluded from the
  calibration window, and the post-2022 rise of the VIIRS/MODIS ratio is
  documented as partly instrumental.
- **The 2018 daytime drop** (item 8): -36% (S-NPP) to -58%/-71% (Terra/Aqua)
  vs 2017, present in all three independent sensors in daytime while night
  counts stay flat. Cause unknown; recorded uncorrected, not speculated
  about.
- **The SP/NRT seam is provisional.** The historical record comes from the
  standard (SP) archive; the recent tail (MODIS from 2026-09-01,
  S-NPP/NOAA-20/21 from 2026-08-01) is near-real-time: unreviewed science
  quality that may be revised, and the seam date moves as FIRMS extends SP.
  The seam is measured per satellite from the stored record
  (`/api/meta.firms_seams`), shown in the Methods panel, and map popups
  label NRT detections "provisional". Values at the seam can shift when SP
  catches up; re-running the backfill replaces NRT-era rows once SP covers
  them.
- **S-NPP end of delivery 2026-11-02** (item 8): the reference series ends
  there; the record continues on NOAA-20 in S-NPP-equivalent units
  (continuity factors, direction: divide). The factor is a ratio of totals
  over 103 shared months - its year-to-year spread (0.84-1.17 on the real
  record) is the honest uncertainty and is carried as an interval.
- **AOI scope** (item 8): every factor, percentile and headline number is
  calibrated for the selected AOI only (presets: nile-delta-egypt). They
  do not transfer to other regions.
- FIRMS CSV granularity bounds the dedup rule (minutes, not seconds) and
  forbids VZA/bin-native FRP correction and explicit cloud compensation;
  both fold into the fitted transfer at aggregate level. Swath-level L2
  products (MOD14/VNP14IMG with geolocation fields) would unlock the full
  Zheng et al. (2026) chain — the engine's module boundaries are already
  shaped for it (`matching` consumes footprint geometry; `moy` consumes
  monthly aggregates).
- AOI-solar day bucketing approximates administrative timezones.
- The seed archive is synthetic. It is labeled as such in the UI
  (`SEED ARCHIVE` chip, `/api/meta.seed_disclaimer`) and in every
  export? — no: exports carry no synthetic marker; treat exported rows as
  platform-internal data, switch to `FIRMS_MAP_KEY` for publication use.

## 11. References

1. Giglio, L., Schroeder, W., Justice, C.O. (2016). The Collection 6 MODIS active fire detection algorithm and fire products. *RSE* 178:31–43.
2. Schroeder, W., Oliva, P., Giglio, L., Csiszar, I. (2014). The new VIIRS 375 m active fire detection data product. *RSE* 143:85–96.
3. Zheng, Y., et al. (2026). Fire Radiative Power Correction and Spatiotemporal Fusion Based on MYD14 and VNP14IMG. *Remote Sensing* 18(10):1650.
4. Vadrevu, K., et al. (2018). Intercomparison of MODIS Aqua and VIIRS I-band fires and emissions in an agricultural landscape. *Remote Sensing* 10(7):978.
5. Li, F., Zhang, X., Kondragunta, S., Csiszar, I. (2018). Comparison of fire radiative power estimates from VIIRS and MODIS observations. *JGR-Atmospheres* 123:4545–4563.
6. Wooster, M.J., Zhukov, B., Oertel, D. (2003). Fire radiative energy for quantitative study of biomass burning. *RSE* 86:83–107.
7. Su, H., Yu, Y. (2026). A Global Dataset of Individual Fire Events (2012–2025) derived from VIIRS Active Fire Product. *ESSD* preprint, doi:10.5194/essd-2026-387.
8. Jolly, W.M., et al. (2015). Climate-induced variations in global wildfire danger from 1979–2013. *Nature Communications* 6:8537.
9. Csiszar, I., et al. (2014). Active fires from the Suomi NPP VIIRS: product status and initial assessment. *JGR-Atmospheres*.
10. NASA FIRMS / LANCE — API and attribute documentation (firms.modaps.eosdis.nasa.gov).
