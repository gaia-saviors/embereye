# AUDIT_REAL.md — Phase 1 findings vs the real FIRMS record

Status: **v1.1 (2026-11-02), audit outputs received and merged.** Phase 1
audited the pipeline on the synthetic calibrated archive; the real backfill
(171,853 rows, nile-delta-egypt, completed 2026-11-02) is the referee.
Verdicts:

- **CONFIRMED** — the real data reproduces the Phase 1 result.
- **CHANGED** — the real data moves the number or the policy; the change is
  specified and implemented (Phase 2, v1.0.5).
- **ARTIFACT-OF-GENERATOR** — a property of the synthetic archive that has no
  counterpart in the real data (or the reverse).

Numbers come from Ali's audit run 2026-11-02 (`all.md` + `monthly_counts.csv`,
produced by `scripts/audit_real.py`). The state.json quiet-chunk accounting
(§5d) is still pending — it must run on the machine that did the backfill.

## 0. Ground truth (real backfill)

- Satellites: Terra 6,752 (2000-11-02..2026-11-01) · Aqua 6,870
  (2002-07-07..2026-10-28) · SNPP 93,372 (2012-01-20..2026-10-31) ·
  N20 46,137 (2018-04-01..2026-10-31) · N21 18,722 (2023-03-01..2026-10-31).
  Derived: harmonized_daily 9,496 · daily_aggregates 14,366 · fire_episodes
  40,639. *(Ali, 2026-11-02)*
- MODIS last pass: rows_in 7,231 kept 7,108, quiet_chunks 138, warnings 468,
  requests 907. *(Ali, 2026-11-02)*

## 1. Ledger

| # | Phase 1 finding (synthetic era) | Verdict | Real-data evidence & action |
|---|---|---|---|
| A1 | Pipeline runs end-to-end on real-shaped data | **CONFIRMED** | Ran unchanged on 171,853 real rows; all derived tables built. |
| A2 | VIIRS/MODIS detection ratio is a stable several-fold factor | **CHANGED (root cause settled)** | All-detections ratios drift 12-40x because the populations are mixed: S-NPP night rows are ~71% static (day 32%). **Day-only, non-static, S-NPP/(Terra+Aqua): 7.39 (2012-17), 7.18 (2012-21), 7.40 (2022-25), yearly range 5.77-9.46, CV ~7-10%** — the prompt's 7.3/7.2/7.5 and 5.8-9.5 confirmed on the full matrix. Month-of-year ratio (day, non-static, 2012-2022) ranges 3.16 (Dec) to 11.29 (Sep) → per-month factors required; one annual scalar would distort the seasonal shape. Phase 2 adopts the comparable-subset month-of-year transfer. |
| A3 | VIIRS platforms may be treated as interchangeable rows of one series | **CHANGED (policy, implemented)** | N20 vs SNPP: Pearson r=0.991, Spearman ρ=0.957; N21 vs SNPP r=0.991 — same fires, **never summed**. harmonized_daily is SNPP-reference from 2012; N20/N21 are cross-checks. Post-S-NPP (2026-11-02): N20 / day-night continuity factor. **Direction fixed (Phase 2 item 5): factor = successor/reference over 103 shared months (day ≈ 1.006, night ≈ 1.054 on the non-static record) > 1 ⇒ N20 HIGHER ⇒ S-NPP-equiv = N20 / factor.** The audit's pooled 1.029 quoted as "x" was backwards; day/night-specific factors are used. Yearly factor spread 0.84-1.17 = the carried uncertainty. |
| A4 | Static-source mask flags a small industrial minority | **CHANGED (classification, implemented)** | Real flagged share ~46% (SNPP). Phase 2: **FIRMS type=2 is the PRIMARY static definition** (SP rows direct + NRT via 2-decimal location inheritance: 67,945 type=2 rows, 0 missed); the 60-day/T4 persistence mask is now a SECONDARY `recurring_source` tag (19,645 persistence-only rows). The crop-like question (17,792 rows ≈ same magnitude as persistence-only) is settled by the new audit cross-tab (persistence-only × crop-like × month-of-year) + type=2-only sensitivity ratio — `audit_real.py static` re-run. Sources stay in totals (challenge text); static share displayed; layers toggle in UI. |
| A5 | Episodes cluster individual fire events | **CONFIRMED** | 40,639 episodes. Static rows excluded by design (a flare is not a spreading fire). |
| A6 | 2018 activity dip exists only where year_factors encode it | **CONFIRMED (real, cause open — documented)** | 2018 vs 2017: Terra −58%, Aqua −71%, SNPP −36%, **daytime only (night flat)**, in all three independent sensors. Documented in Methods/LIMITATIONS as real-but-unexplained; no correction applied, no speculation. |
| A7 | MODIS→VIIRS transfer: Theil–Sen power law on log1p monthly sums | **CHANGED (implemented)** | Retired: LOYO bias −15% to −23% (Terra 22.6%/−22.6%, Aqua 15.1%/−15.1%). **Adopted (Phase 2): smoothed month-of-year factors on the comparable subset, calibrated 2012-2022, LOYO-validated: pooled MAPE 7.5%, bias +1.6%, 95% PI [−14.3%, +17.8%]** (per-platform 11.3% → pooled wins by the 1pp margin rule; rule documented). Interval carried into `harmonized_daily.n_harmonized_lo/hi` + UI band. Power-law kept as Methods comparison column. |
| A8 | Seam handling: SP archive assumed to run to the present | **CHANGED (v1.0.2)** | SP max_date 2026-08-31 (MODIS) / 2026-07-31 (VIIRS×3); NRT tail provisional; 2026 monthly seam table verified healthy — SP falls to 0 the month after each platform's SP max_date, NRT takes over, no both-zero months. |
| A9 | Chunking: v1.0.2 DAY_RANGE default 10, end-anchored | **CHANGED (v1.0.3)** | Official contract 1..5 forward-anchored; `--verify-chunking` gate; windows re-walked. |
| A10 | "Raw per-chunk CSVs are stored verbatim" | **CHANGED (v1.0.4)** | Every chunk saved verbatim pre-parse; survives `--fresh`. |
| A11 | DB plumbing: scripts silently fall back to SQLite | **CHANGED (v1.0.4)** | Scripts exit 2 without explicit DB URL; `--sqlite` opt-in; `--status` truth. *(audit note: this run used `--sqlite embereye_dev.db` explicitly.)* |
| A12 | `--fresh` wipes an AOI's rows | **CHANGED (v1.0.4)** | Scoped to `--sensors` + confirmation; `--fresh-all-sensors` explicit. |
| A13 | Synthetic archive properties (ratio regime, static cells, seasonality, year_factors) | **ARTIFACT-OF-GENERATOR** | None survive in the real record; real seasonality (Sep-Oct peak, 49%/21% of annual comparable counts), static share and annual variability come from the actual data. Seed stays as labelled offline fallback. |
| A14 | Real-data features the generator never produced | **CONFIRMED (real, handled)** | SP/NRT schema seam (no type column, 1-4 digit acq_time, "2.0NRT"), FIRMS rate limits, orbit drift signal: Terra overpass hour Spearman ρ(year, hour) = −0.50 and Aqua +0.67 — **drift starts 2023** (Terra 8.8→7.1 UTC, Aqua 11.2→13.1 UTC); calibration therefore stops at 2022 and the post-2022 ratio rise is documented as partly instrumental. Coverage per satellite-year complete (no fetch-gap holes; 2001 Terra/2018 Aqua thin years are real quiet years, not gaps). Aqua tail 2026-10-28 vs Terra 2026-11-01: afternoon gap, not a product split — raw CSV check queued with raw saving live. |

## 2. Open cells

| Question | Status |
|---|---|
| state.json quiet-chunk / warning accounting (138 / 468 disposition) | **PENDING** — run `audit_real.py anomalies --state <path>` on the machine that did the backfill (Ali re-runs). |
| Persistence-only cross-tab verdict (crop-like vs flare) | Runs with `audit_real.py static` after v1.0.5 deploy; decides nothing by itself — the share display surfaces it either way. |
| Aqua 2026-10 tail raw-CSV check | Queued once raw chunk copies cover the tail (raw saving live since v1.0.4). |

## 3. Decisions locked (implemented in v1.0.5)

1. Comparable-subset month-of-year transfer (A2/A7) with LOYO interval in the series + UI.
2. Weekly/monthly canonical resolution; daily view carries the interval (A7).
3. Reference-platform policy; N20/N21 never summed; continuity after 2026-11-02 with corrected direction (A3).
4. Static = type=2 primary; persistence = secondary `recurring_source` tag; static share always displayed; layers opt-in (A4).
5. LOYO empirical percentile anomalies with min-count guard; visible "unusually quiet" (A6 follow-up).
6. Season censoring for partial years.
7. Headline analytic: current month vs historical median of the same calendar month.
8. LIMITATIONS documented (orbit drift 2023, 2018 daytime drop, SP/NRT seam, S-NPP end, Nile-Delta-only scope).

Run order on Ali's machine (repo root):

```powershell
python scripts\audit_real.py all --sqlite .\embereye_dev.db
# v1.0.5: the `static` section now carries the Phase 2 cross-tab + sensitivity
```
