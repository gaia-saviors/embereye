import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import type { CalendarCell, CalendarResponse, TransferredWeek } from "../api/types";
import { useStore, PROVENANCE_LABEL, PROVENANCE_COLOR } from "../store";
import { calendarCacheKey, getCachedCalendar, setCachedCalendar } from "../lib/calendarCache";
import { rampColor, SENSOR_COLOR, ANOMALY_HIGH, ANOMALY_LOW } from "../lib/colors";
import { fmtInt, fmtMw, fmtNum, MONTHS, MONTH_DOY_START, prettyDate } from "../lib/format";

const GUTTER = 48;
const ROW_H = 21;
const TOP = 24;
const ESTIMATED_HATCH_ID = "estimated-hatch";

export default function CalendarView() {
  const aoiId = useStore((s) => s.aoiId);
  const aois = useStore((s) => s.aois);
  const calendar = useStore((s) => s.calendar);
  const setCalendar = useStore((s) => s.setCalendar);
  const selected = useStore((s) => s.selectedCellDate);
  const setSelected = useStore((s) => s.setSelectedCellDate);
  const setCursorDate = useStore((s) => s.setCursorDate);
  const setView = useStore((s) => s.setView);
  const meta = useStore((s) => s.meta);
  const filters = useStore((s) => s.filters);
  const anomalyOverlay = filters.anomalyOverlay;

  const yearFilter = useStore((s) => s.calendarYearFilter);
  const setYearFilter = useStore((s) => s.setCalendarYearFilter);
  const matrixView = useStore((s) => s.calendarMatrixView);
  const setMatrixView = useStore((s) => s.setCalendarMatrixView);
  const colorRamp = useStore((s) => s.calendarColorRamp);
  const setColorRamp = useStore((s) => s.setCalendarColorRamp);

  const [hover, setHover] = useState<
    | { kind: "day"; cell: CalendarCell; x: number; y: number }
    | { kind: "week"; week: TransferredWeek; x: number; y: number }
    | { kind: "month"; year: number; month: number; total: number; frp: number; deltaPct: number; isEstimated: boolean; x: number; y: number }
    | null
  >(null);
  const [p99Day, setP99Day] = useState(50);
  const [p99Week, setP99Week] = useState(200);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1100);

  const activeAoi = aois.find((a) => a.id === aoiId);

  const startYear = yearFilter === "last10" ? 2016 : yearFilter === "viirs" ? 2012 : undefined;
  const { sensor, confidence, daynight, minFrp, hideStaticSources } = filters;
  const cacheKey = aoiId
    ? calendarCacheKey(aoiId, yearFilter, {
        sensor, confidence, daynight, minFrp, hideStaticSources,
      })
    : "";

  // Human-readable summary of what the matrix is currently counting. With no
  // ribbon filter it is the stored harmonized estimate (comparable daytime,
  // non-static subset); any active filter switches the backend to a raw
  // hotspot slice with exactly the map's predicate.
  const filtersActive =
    sensor !== "all" ||
    confidence !== "all" ||
    daynight !== "all" ||
    minFrp != null ||
    hideStaticSources;
  const filterSummary = filtersActive
    ? [
        "raw detections",
        sensor === "all" ? "all sensors" : sensor,
        daynight === "all" ? "day + night" : daynight === "D" ? "day only" : "night only",
        confidence === "all" ? "all confidence" : `${confidence.toLowerCase()} confidence`,
        minFrp != null ? `FRP ≥ ${minFrp} MW` : null,
        hideStaticSources ? "static flares excluded" : "static flares included",
      ]
        .filter(Boolean)
        .join(" · ")
    : "harmonized estimate (comparable daytime, non-static) · no ribbon filter applied";

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      setWidth(Math.max(800, entries[0].contentRect.width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!aoiId || !cacheKey) return;

    const adopt = (c: CalendarResponse) => {
      setCalendar(c);
      const nativeCells = (c.cells ?? []).filter((x) => x.provenance === "VIIRS_NATIVE");
      const ns = nativeCells.map((x) => x.n_harmonized ?? 0).sort((a, b) => a - b);
      setP99Day(ns.length ? Math.max(1, ns[Math.floor(ns.length * 0.99)]) : 50);
      const ws = (c.transferred_weeks ?? []).map((w) => w.n_harmonized ?? 0).sort((a, b) => a - b);
      setP99Week(ws.length ? Math.max(1, ws[Math.floor(ws.length * 0.99)]) : 200);
    };

    // Tab switches remount this view; a cache hit repaints instantly instead
    // of re-requesting the same payload (and flashing the full-screen loader).
    const cached = getCachedCalendar(cacheKey);
    if (cached) {
      setError(null);
      setLoading(false);
      adopt(cached);
      return;
    }

    let alive = true;
    setLoading(true);
    setError(null);
    api
      .calendar(aoiId, {
        startYear,
        sensor,
        confidence,
        daynight,
        minFrp,
        hideStatic: hideStaticSources,
      })
      .then((c) => {
        if (!alive) return;
        setCachedCalendar(cacheKey, c);
        adopt(c);
      })
      .catch((e) => alive && setError((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [aoiId, cacheKey, startYear, sensor, confidence, daynight, minFrp, hideStaticSources, setCalendar]);

  const years = calendar?.summary.years ?? [];
  const viirsStart = calendar?.viirs_start_date ?? null;
  const seamYear = viirsStart ? Number(viirsStart.slice(0, 4)) : null;
  const seamDoy = useMemo(() => {
    if (!viirsStart) return null;
    return Math.max(
      1,
      Math.ceil(
        (Date.parse(viirsStart + "T00:00:00Z") -
          Date.parse(`${viirsStart.slice(0, 4)}-01-01T00:00:00Z`)) /
          86_400_000,
      ) + 1,
    );
  }, [viirsStart]);

  const cellByDate = useMemo(() => {
    const m = new Map<string, CalendarCell>();
    for (const c of calendar?.cells ?? []) m.set(c.date, c);
    return m;
  }, [calendar]);

  const weeksByYear = useMemo(() => {
    const m = new Map<number, TransferredWeek[]>();
    for (const w of calendar?.transferred_weeks ?? []) {
      const list = m.get(w.year) ?? [];
      list.push(w);
      m.set(w.year, list);
    }
    return m;
  }, [calendar]);

  // Annual totals for each year
  const annualTotals = useMemo(() => {
    const map = new Map<number, number>();
    if (!calendar) return map;
    for (const year of years) {
      const isTransferredYear = seamYear !== null && year < seamYear;
      if (isTransferredYear) {
        const weeks = weeksByYear.get(year) ?? [];
        const sum = weeks.reduce((acc, w) => acc + (w.n_harmonized || 0), 0);
        map.set(year, Math.round(sum));
      } else {
        const yearCells = calendar.cells.filter((c) => c.year === year);
        const sum = yearCells.reduce((acc, c) => acc + (c.n_harmonized || 0), 0);
        map.set(year, Math.round(sum));
      }
    }
    return map;
  }, [calendar, years, seamYear, weeksByYear]);

  const maxAnnualTotal = useMemo(() => {
    let max = 1;
    for (const val of annualTotals.values()) {
      if (val > max) max = val;
    }
    return max;
  }, [annualTotals]);

  // Monthly metrics & NASA challenge comparison (Current Month vs. Historical Median)
  const monthMetrics = useMemo(() => {
    if (!calendar || !calendar.cells.length) return null;
    const byYearMonth: Record<number, Record<number, { n: number; frp: number; days: number; isTransferred: boolean }>> = {};
    for (const c of calendar.cells) {
      const m = Number(c.date.slice(5, 7));
      if (!byYearMonth[c.year]) byYearMonth[c.year] = {};
      if (!byYearMonth[c.year][m]) {
        byYearMonth[c.year][m] = {
          n: 0,
          frp: 0,
          days: 0,
          isTransferred: c.provenance === "MODIS_TRANSFERRED" || c.provenance === "MODIS_ONLY",
        };
      }
      byYearMonth[c.year][m].n += c.n_harmonized || 0;
      byYearMonth[c.year][m].frp += c.frp_harmonized_mw || 0;
      byYearMonth[c.year][m].days += 1;
    }

    // Incorporate weekly transferred records for pre-2012 years if cells are absent
    for (const w of calendar.transferred_weeks || []) {
      const m = Number(w.start_date.slice(5, 7));
      if (!byYearMonth[w.year]) byYearMonth[w.year] = {};
      if (!byYearMonth[w.year][m] || byYearMonth[w.year][m].days === 0) {
        if (!byYearMonth[w.year][m]) {
          byYearMonth[w.year][m] = { n: 0, frp: 0, days: 0, isTransferred: true };
        }
        byYearMonth[w.year][m].n += w.n_harmonized || 0;
        byYearMonth[w.year][m].days += w.n_days_with_data;
      }
    }

    // Find all-time peak month
    let peakVal = 0;
    let peakYear = 2000;
    let peakM = 10;
    for (const [yStr, mObj] of Object.entries(byYearMonth)) {
      const y = Number(yStr);
      for (const [mStr, d] of Object.entries(mObj)) {
        if (d.n > peakVal) {
          peakVal = d.n;
          peakYear = y;
          peakM = Number(mStr);
        }
      }
    }

    // Latest month
    const sortedYears = Object.keys(byYearMonth).map(Number).sort((a, b) => a - b);
    const lastYear = sortedYears[sortedYears.length - 1];
    const monthsInLast = Object.keys(byYearMonth[lastYear] || {}).map(Number).sort((a, b) => a - b);
    const lastM = monthsInLast[monthsInLast.length - 1] || 10;
    const latestVal = byYearMonth[lastYear]?.[lastM]?.n || 0;

    // Historical median for that month across complete prior years
    const histVals: number[] = [];
    for (const y of sortedYears) {
      if (y === lastYear) continue;
      const v = byYearMonth[y]?.[lastM]?.n;
      if (typeof v === "number" && v > 0) histVals.push(v);
    }
    histVals.sort((a, b) => a - b);
    const median = histVals.length ? histVals[Math.floor(histVals.length / 2)] : latestVal;
    const deltaPct = median > 0 ? ((latestVal - median) / median) * 100 : 0;

    // Max monthly value for color scaling
    let maxMonthly = 1;
    for (const mObj of Object.values(byYearMonth)) {
      for (const d of Object.values(mObj)) {
        if (d.n > maxMonthly) maxMonthly = d.n;
      }
    }

    // Medians per month 1..12
    const mediansByMonth: Record<number, number> = {};
    for (let m = 1; m <= 12; m++) {
      const vals: number[] = [];
      for (const y of sortedYears) {
        const v = byYearMonth[y]?.[m]?.n;
        if (typeof v === "number" && v > 0) vals.push(v);
      }
      vals.sort((a, b) => a - b);
      mediansByMonth[m] = vals.length ? vals[Math.floor(vals.length / 2)] : 0;
    }

    return {
      byYearMonth,
      mediansByMonth,
      latestYear: lastYear,
      latestMonth: lastM,
      latestMonthName: MONTHS[lastM - 1],
      latestTotal: Math.round(latestVal),
      histMedian: Math.round(median),
      deltaPct,
      peakYear,
      peakMonth: peakM,
      peakMonthStr: `${MONTHS[peakM - 1]} ${peakYear}`,
      peakTotal: Math.round(peakVal),
      maxMonthly,
    };
  }, [calendar]);

  // KPI Calculations
  const kpis = useMemo(() => {
    if (!calendar || !calendar.cells.length) {
      return {
        spanLabel: "2000–2026 · 26 Years",
        modisSplit: "MODIS: 2000–2011",
        viirsSplit: "VIIRS: 2012–2026",
        totalVolume: 0,
        dailyRate: 0,
        anomaliesHighCount: 0,
        calibration: "Theil-Sen Log-Linear · R² = 0.852",
      };
    }
    const totalVol = Math.round(calendar.cells.reduce((acc, c) => acc + (c.n_harmonized || 0), 0));
    const meanDaily = calendar.cells.length ? (totalVol / calendar.cells.length).toFixed(1) : "0";
    const highAnomalies = calendar.summary.n_anomaly_high ?? calendar.cells.filter((c) => c.flag_high).length;
    const firstY = years.length ? years[0] : 2000;
    const lastY = years.length ? years[years.length - 1] : 2026;
    const spanYrs = lastY - firstY + 1;

    return {
      spanLabel: `${firstY}–${lastY} · ${spanYrs} Years`,
      modisSplit: "MODIS era: 2000–2011 (weekly transferred)",
      viirsSplit: "VIIRS era: 2012–2026 (daily calibrated)",
      totalVolume: totalVol,
      dailyRate: Number(meanDaily),
      anomaliesHighCount: highAnomalies,
      calibration: meta?.methodology.harmonization_tier ?? "Theil-Sen Log-Linear · R² = 0.852",
    };
  }, [calendar, years, meta]);

  // Available SVG layout widths: expand across 100% of container with zero dead space
  const inspectorOpen = Boolean(selected);
  const containerW = Math.max(760, width - (inspectorOpen ? 340 : 40));
  const annualBarAreaW = Math.max(160, Math.min(230, Math.round(containerW * 0.17)));
  const annualBarMaxW = annualBarAreaW - 65;
  const daysAreaW = containerW - GUTTER - annualBarAreaW - 16;
  const cellW = Math.max(1.8, daysAreaW / 366);
  const rightBarOffset = GUTTER + 366 * cellW + 12;
  const svgW = containerW;
  const svgH = TOP + years.length * ROW_H + 28;

  const selectedCell = selected ? cellByDate.get(selected) ?? null : null;

  if (!aoiId) {
    return <Empty message="Select an area of interest from the control bar above." />;
  }
  if (loading) return <Empty message="Harmonizing multi-decadal fire records..." spinner />;
  if (error) {
    const isNoData = error.includes("404") || error.includes("no calendar data");
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 text-center">
        <div className="max-w-md rounded-2xl border border-basalt-300 bg-basalt-100 p-8 shadow-2xl">
          <div className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full bg-terra/10 text-2xl text-terra">
            🛰️
          </div>
          <h3 className="mb-2 font-display text-base font-bold text-alabaster">
            {isNoData ? "NASA FIRMS Archive Not Backfilled" : "Calendar Unavailable"}
          </h3>
          <p className="mb-5 text-xs leading-relaxed text-pewter">
            {isNoData
              ? "The v1.0.8 offline database includes a verified NASA FIRMS archive for Nile Delta, Egypt. Other preset regions in that snapshot use synthetic demo records; custom regions use loaded observations that fall inside their polygon."
              : error}
          </p>
          <button
            onClick={() => useStore.getState().setAoiId("nile-delta-egypt")}
            className="rounded-lg bg-terra px-5 py-2.5 font-display text-xs font-bold text-alabaster shadow-md transition hover:bg-ember-400"
          >
            Switch to Nile Delta (Active Archive)
          </button>
        </div>
      </div>
    );
  }
  if (!calendar) return <Empty message="No calendar data available." />;

  const hasWeekly = (calendar.transferred_weeks?.length ?? 0) > 0;

  return (
    <div ref={wrapRef} className="h-full overflow-y-auto bg-basalt p-4 flex flex-col gap-3.5">
      {/* ── Top Bento KPI Strip (4 Cards) ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 shrink-0">
        {/* Card 1: Active Archive Span */}
        <div className="rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold tracking-widest text-muted uppercase font-mono">Archive Span</span>
            <span className="h-2 w-2 rounded-full bg-amber" />
          </div>
          <div className="my-1.5 font-display text-lg font-bold text-alabaster tracking-tight">
            {kpis.spanLabel}
          </div>
          <div className="text-[10px] text-pewter flex flex-col gap-0.5">
            <span className="truncate">{kpis.modisSplit}</span>
            <span className="truncate">{kpis.viirsSplit}</span>
          </div>
        </div>

        {/* Card 2: Total Harmonized Volume */}
        <div className="rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold tracking-widest text-muted uppercase font-mono">Harmonized Volume</span>
            <span className="h-2 w-2 rounded-full bg-terra" />
          </div>
          <div className="my-1.5 font-mono text-lg font-bold text-terra">
            {fmtNum(kpis.totalVolume)} <span className="text-xs font-normal text-pewter font-sans">detections</span>
          </div>
          <div className="text-[10px] text-pewter">
            Mean daily: <span className="font-mono text-alabaster font-semibold">{kpis.dailyRate}</span> · <span className="font-mono text-coral">{fmtInt(kpis.anomaliesHighCount)}</span> p95 anomalies
          </div>
        </div>

        {/* Card 3: NASA Challenge Comparison — Current Month vs Historical Median */}
        <div className="rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold tracking-widest text-muted uppercase font-mono">
              Latest Month vs. Median
            </span>
            <span className={`h-2 w-2 rounded-full ${(monthMetrics?.deltaPct ?? 0) >= 0 ? "bg-coral" : "bg-jade"}`} />
          </div>
          <div className="my-1 flex items-baseline gap-2">
            <span className="font-mono text-lg font-bold text-alabaster">
              {monthMetrics?.latestMonthName} {monthMetrics?.latestYear}
            </span>
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-[11px] font-bold ${
                (monthMetrics?.deltaPct ?? 0) >= 10
                  ? "bg-coral/15 text-coral"
                  : (monthMetrics?.deltaPct ?? 0) <= -10
                  ? "bg-jade/15 text-jade"
                  : "bg-basalt-300 text-pewter"
              }`}
            >
              {(monthMetrics?.deltaPct ?? 0) >= 0 ? "+" : ""}
              {(monthMetrics?.deltaPct ?? 0).toFixed(1)}%
            </span>
          </div>
          <div className="text-[10px] text-pewter truncate">
            Median: <span className="font-mono text-alabaster font-semibold">{fmtInt(monthMetrics?.histMedian)}</span> · Peak: <span className="font-mono text-amber">{monthMetrics?.peakMonthStr} ({fmtInt(monthMetrics?.peakTotal)})</span>
          </div>
        </div>

        {/* Card 4: Cross-Sensor Calibration */}
        <div className="rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold tracking-widest text-muted uppercase font-mono">Sensor Calibration</span>
            <span className="h-2 w-2 rounded-full bg-jade" />
          </div>
          <div className="my-1.5 font-mono text-2xl font-semibold text-[#10B981]">
            R² = 0.852
          </div>
          <div className="text-[10px] text-pewter truncate">
            Comparable-subset transfer · MODIS 1km → VIIRS 375m
          </div>
        </div>
      </div>

      {/* ── Main Bento Card (Calendar Matrix + Inspector) ── */}
      <div className="relative rounded-2xl border border-basalt-300 bg-basalt-100 p-4 shadow-md flex-1 flex flex-col min-h-0">
        {/* Card Header & Controls */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-basalt-300 pb-3 mb-3">
          <div>
            <h2 className="font-display text-sm font-bold text-alabaster tracking-wide">
              {activeAoi?.name ?? calendar.aoi_id.toUpperCase()} — Harmonized Burning Calendar
            </h2>
            <div className="text-[11px] text-pewter flex items-center gap-2 mt-0.5">
              <span>{matrixView === "daily" ? "52-Week Multi-Decadal Heatmap" : "12-Month Multi-Decadal Heatmap"}</span>
              <span>·</span>
              <span className="font-mono text-xs text-terra font-semibold">
                {matrixView === "daily" ? "Daily (2012+) & Weekly (2000–2011)" : "Monthly Aggregations (2000–2026)"}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
              <span className="rounded bg-basalt-200 px-1.5 py-0.5 font-mono font-bold uppercase tracking-widest text-muted">
                Ribbon filters
              </span>
              <span className="text-pewter">{filterSummary}</span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {/* View Mode Toggle: Daily DOY vs Monthly */}
            <div className="flex items-center rounded-lg border border-basalt-300 bg-basalt-200 p-0.5 text-[11px] font-display">
              <button
                onClick={() => setMatrixView("daily")}
                className={`rounded-md px-2.5 py-1 font-semibold transition ${
                  matrixView === "daily" ? "bg-terra text-alabaster shadow-sm" : "text-pewter hover:text-alabaster"
                }`}
              >
                Daily DOY
              </button>
              <button
                onClick={() => setMatrixView("monthly")}
                className={`rounded-md px-2.5 py-1 font-semibold transition ${
                  matrixView === "monthly" ? "bg-terra text-alabaster shadow-sm" : "text-pewter hover:text-alabaster"
                }`}
              >
                Monthly (Jan–Dec)
              </button>
            </div>

            {/* Accessible Palette Selector */}
            <div className="flex items-center rounded-lg border border-basalt-300 bg-basalt-200 p-0.5 text-[10px] font-mono">
              <button
                onClick={() => setColorRamp("magma")}
                className={`rounded-md px-2 py-1 transition ${
                  colorRamp === "magma" ? "bg-basalt-400 text-alabaster font-bold" : "text-muted hover:text-alabaster"
                }`}
                title="Volcanic Magma Ramp"
              >
                Magma
              </button>
              <button
                onClick={() => setColorRamp("viridis")}
                className={`rounded-md px-2 py-1 transition ${
                  colorRamp === "viridis" ? "bg-basalt-400 text-alabaster font-bold" : "text-muted hover:text-alabaster"
                }`}
                title="Accessible Viridis (Perceptually Uniform)"
              >
                Viridis
              </button>
              <button
                onClick={() => setColorRamp("cividis")}
                className={`rounded-md px-2 py-1 transition ${
                  colorRamp === "cividis" ? "bg-basalt-400 text-alabaster font-bold" : "text-muted hover:text-alabaster"
                }`}
                title="Colorblind-Optimized Cividis"
              >
                Cividis
              </button>
            </div>

            {/* Color scale legend */}
            <div className="flex items-center gap-1.5 text-[10px] font-mono text-pewter bg-basalt-200 px-2.5 py-1.5 rounded-lg border border-basalt-300">
              <span>0</span>
              <span
                className={`inline-block h-2 w-20 rounded-sm ${
                  colorRamp === "viridis" ? "viridis-ramp" : colorRamp === "cividis" ? "cividis-ramp" : "magma-ramp"
                }`}
              />
              <span>{matrixView === "daily" ? `p99 (${fmtInt(p99Day)}/d)` : `Max (${fmtNum(monthMetrics?.maxMonthly ?? 1000)})`}</span>
            </div>

            {/* Year window filter */}
            <div className="flex items-center rounded-lg border border-basalt-300 bg-basalt-200 p-0.5 text-[11px] font-display">
              <button
                onClick={() => setYearFilter("all")}
                className={`rounded-md px-2.5 py-1 font-semibold transition ${
                  yearFilter === "all" ? "bg-terra text-alabaster shadow-sm" : "text-pewter hover:text-alabaster"
                }`}
              >
                All 2000–2026
              </button>
              <button
                onClick={() => setYearFilter("last10")}
                className={`rounded-md px-2.5 py-1 font-semibold transition ${
                  yearFilter === "last10" ? "bg-terra text-alabaster shadow-sm" : "text-pewter hover:text-alabaster"
                }`}
              >
                Last 10 Yrs
              </button>
              <button
                onClick={() => setYearFilter("viirs")}
                className={`rounded-md px-2.5 py-1 font-semibold transition ${
                  yearFilter === "viirs" ? "bg-terra text-alabaster shadow-sm" : "text-pewter hover:text-alabaster"
                }`}
              >
                VIIRS Era 2012+
              </button>
            </div>
          </div>
        </div>

        {/* Workspace: Heatmap SVG + Right Inspector */}
        <div className="flex-1 flex overflow-hidden min-h-0 relative">
          <div className="flex-1 overflow-auto pr-2">
            <svg
              width={svgW}
              height={svgH}
              className="block select-none"
              onMouseLeave={() => setHover(null)}
              role="img"
              aria-label="Multi-year burning activity calendar"
            >
              <defs>
                <pattern id={ESTIMATED_HATCH_ID} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="5" height="5" fill="transparent" />
                  <line x1="0" y1="0" x2="0" y2="5" stroke="#E5A93C" strokeWidth="0.9" opacity="0.45" />
                </pattern>
              </defs>

              {/* Month ruler */}
              {matrixView === "daily" ? (
                MONTH_DOY_START.map((doy, i) => (
                  <g key={i}>
                    <text x={GUTTER + doy * cellW} y={12} fontSize={10} fill="#A09CA8" fontFamily="Inter, sans-serif">
                      {MONTHS[i]}
                    </text>
                    <line
                      x1={GUTTER + doy * cellW} y1={TOP - 6}
                      x2={GUTTER + doy * cellW} y2={TOP + years.length * ROW_H}
                      stroke="#25232C" strokeWidth={i === 0 ? 0 : 1}
                    />
                  </g>
                ))
              ) : (
                MONTHS.map((mName, i) => {
                  const monthColW = (daysAreaW - 11 * 4) / 12;
                  const mx = GUTTER + i * (monthColW + 4);
                  return (
                    <g key={mName}>
                      <text x={mx + monthColW / 2} y={12} textAnchor="middle" fontSize={10} fill="#A09CA8" fontFamily="Inter, sans-serif" fontWeight="600">
                        {mName}
                      </text>
                    </g>
                  );
                })
              )}

              {/* Annual Totals Column Header */}
              <text
                x={rightBarOffset}
                y={12}
                fontSize={10}
                fill="#A09CA8"
                fontFamily="Inter, sans-serif"
                fontWeight="600"
              >
                ANNUAL TOTALS
              </text>

              {/* Year rows */}
              {years.map((year, yi) => {
                const y = TOP + yi * ROW_H;
                const isTransferredYear = seamYear === null ? false : year < seamYear;
                const isSeamYear = seamYear !== null && year === seamYear;
                const annualSum = annualTotals.get(year) ?? 0;
                const barWidth = Math.max(3, (annualSum / maxAnnualTotal) * annualBarMaxW);

                return (
                  <g key={year}>
                    {/* Year Label */}
                    <text
                      x={GUTTER - 8}
                      y={y + 14}
                      fontSize={10}
                      fill="#A09CA8"
                      textAnchor="end"
                      fontFamily="JetBrains Mono, monospace"
                      fontWeight="500"
                    >
                      {year}
                    </text>

                    {/* Row Background Track */}
                    <rect
                      x={GUTTER}
                      y={y + 1}
                      width={matrixView === "daily" ? 366 * cellW : daysAreaW}
                      height={ROW_H - 4}
                      fill="#141318"
                      stroke="#25232C"
                      strokeWidth={0.5}
                      rx={2}
                    />

                    {/* Monthly Tiles Mode */}
                    {matrixView === "monthly" &&
                      Array.from({ length: 12 }, (_, i) => i + 1).map((m) => {
                        const monthColW = (daysAreaW - 11 * 4) / 12;
                        const mx = GUTTER + (m - 1) * (monthColW + 4);
                        const mData = monthMetrics?.byYearMonth[year]?.[m];
                        const val = mData?.n ?? 0;
                        const frp = mData?.frp ?? 0;
                        const isEst = mData?.isTransferred ?? (isTransferredYear || isSeamYear);
                        const histMed = monthMetrics?.mediansByMonth[m] ?? 0;
                        const delta = histMed > 0 ? ((val - histMed) / histMed) * 100 : 0;
                        const t = Math.min(1, val / (monthMetrics?.maxMonthly || 1));

                        return (
                          <g key={`m-${year}-${m}`}>
                            <rect
                              x={mx}
                              y={y + 1}
                              width={monthColW}
                              height={ROW_H - 4}
                              rx={2}
                              fill={val > 0 ? rampColor(Math.pow(t, 0.6), colorRamp) : "#141318"}
                              className="cursor-pointer transition-colors"
                              onMouseEnter={(e) =>
                                setHover({
                                  kind: "month",
                                  year,
                                  month: m,
                                  total: val,
                                  frp,
                                  deltaPct: delta,
                                  isEstimated: isEst,
                                  x: e.clientX,
                                  y: e.clientY,
                                })
                              }
                            />
                            {isEst && (
                              <rect
                                x={mx}
                                y={y + 1}
                                width={monthColW}
                                height={ROW_H - 4}
                                rx={2}
                                fill={`url(#${ESTIMATED_HATCH_ID})`}
                                pointerEvents="none"
                              />
                            )}
                            {monthColW >= 28 && val > 0 && (
                              <text
                                x={mx + monthColW / 2}
                                y={y + (ROW_H - 4) / 2 + 3.5}
                                textAnchor="middle"
                                fontSize={9}
                                fill="#F4F3F6"
                                fontFamily="JetBrains Mono, monospace"
                                fontWeight="500"
                                pointerEvents="none"
                                opacity={0.9}
                              >
                                {fmtNum(val)}
                              </text>
                            )}
                          </g>
                        );
                      })}

                    {/* Daily Mode (DOY + Weekly Transferred) */}
                    {matrixView === "daily" && (
                      <>
                        {/* Weekly transferred cells (pre-seam) */}
                        {(isTransferredYear || isSeamYear) &&
                          (weeksByYear.get(year) ?? []).map((w) => {
                            const startDoy = dayOfYear(w.start_date);
                            const endDoy = dayOfYear(w.end_date);
                            const x = GUTTER + (startDoy - 1) * cellW;
                            const wWidth = Math.max(cellW, (endDoy - startDoy + 1) * cellW - 0.6);
                            const t = Math.min(1, w.n_harmonized / p99Week);
                            return (
                              <g key={`w-${w.start_date}`}>
                                <rect
                                  x={x}
                                  y={y + 1}
                                  width={wWidth}
                                  height={ROW_H - 4}
                                  fill={w.n_harmonized > 0 ? rampColor(Math.pow(t, 0.6), colorRamp) : "#141318"}
                                  opacity={0.88}
                                  className="cursor-pointer"
                                  onMouseEnter={(e) => setHover({ kind: "week", week: w, x: e.clientX, y: e.clientY })}
                                  onClick={() => setSelected(w.start_date)}
                                />
                                <rect
                                  x={x}
                                  y={y + 1}
                                  width={wWidth}
                                  height={ROW_H - 4}
                                  fill={`url(#${ESTIMATED_HATCH_ID})`}
                                  pointerEvents="none"
                                />
                                {anomalyOverlay && w.flag_high && (
                                  <rect
                                    x={x - 0.7}
                                    y={y + 0.3}
                                    width={wWidth + 1.4}
                                    height={ROW_H - 4 + 1.4}
                                    fill="none"
                                    stroke={ANOMALY_HIGH}
                                    strokeWidth={1.2}
                                    pointerEvents="none"
                                  />
                                )}
                                {anomalyOverlay && w.flag_low && (
                                  <circle
                                    cx={x + wWidth / 2}
                                    cy={y + 1 + (ROW_H - 4) / 2}
                                    r={2}
                                    fill={ANOMALY_LOW}
                                    pointerEvents="none"
                                  />
                                )}
                              </g>
                            );
                          })}

                        {/* Daily cells: VIIRS-native era */}
                        {calendar.cells
                          .filter((c) => c.year === year)
                          .filter((c) => seamDoy === null || seamYear === null || year > seamYear || c.doy >= seamDoy)
                          .filter((c) => c.provenance !== "MODIS_TRANSFERRED" && c.provenance !== "MODIS_ONLY")
                          .map((c) => {
                            const t = Math.min(1, c.n_harmonized / p99Day);
                            const isSel = selected === c.date;
                            const flaggedHigh = anomalyOverlay && c.flag_high;
                            const flaggedLow = anomalyOverlay && c.flag_low;
                            return (
                              <g key={c.date}>
                                <rect
                                  x={GUTTER + (c.doy - 1) * cellW}
                                  y={y + 1}
                                  width={Math.max(1.2, cellW - 0.4)}
                                  height={ROW_H - 4}
                                  fill={c.provenance === "NO_DATA" ? "#141318" : rampColor(Math.pow(t, 0.6), colorRamp)}
                                  stroke={isSel ? "#FFF3B0" : undefined}
                                  strokeWidth={isSel ? 1.4 : 0}
                                  className="cursor-pointer"
                                  onMouseEnter={(e) => setHover({ kind: "day", cell: c, x: e.clientX, y: e.clientY })}
                                  onClick={() => setSelected(c.date)}
                                />
                                {flaggedLow && (
                                  <circle
                                    cx={GUTTER + (c.doy - 1) * cellW + Math.max(1.2, cellW - 0.4) / 2}
                                    cy={y + 1 + (ROW_H - 4) / 2}
                                    r={1.8}
                                    fill={ANOMALY_LOW}
                                    pointerEvents="none"
                                  />
                                )}
                                {flaggedHigh && (
                                  <rect
                                    x={GUTTER + (c.doy - 1) * cellW - 0.7}
                                    y={y + 0.3}
                                    width={Math.max(1.2, cellW - 0.4) + 1.4}
                                    height={ROW_H - 4 + 1.4}
                                    fill="none"
                                    stroke={ANOMALY_HIGH}
                                    strokeWidth={1.2}
                                    pointerEvents="none"
                                  />
                                )}
                              </g>
                            );
                          })}

                        {/* Season onset/end markers */}
                        {calendar.seasons
                          .filter((s) => s.year === year && s.onset_date)
                          .map((s) => {
                            const onset = cellByDate.get(s.onset_date!);
                            const end = s.end_date ? cellByDate.get(s.end_date!) : null;
                            return (
                              <g key={s.year}>
                                {onset && (
                                  <polygon
                                    points={`${GUTTER + (onset.doy - 1) * cellW + cellW / 2},${y - 4} ${GUTTER + (onset.doy - 1) * cellW},${y + 1} ${GUTTER + (onset.doy - 1) * cellW + cellW},${y + 1}`}
                                    fill="#E04F33"
                                  />
                                )}
                                {end && (
                                  <polygon
                                    points={`${GUTTER + (end.doy - 1) * cellW + cellW / 2},${y + ROW_H - 5} ${GUTTER + (end.doy - 1) * cellW},${y + ROW_H - 10} ${GUTTER + (end.doy - 1) * cellW + cellW},${y + ROW_H - 10}`}
                                    fill="#A09CA8"
                                  />
                                )}
                              </g>
                            );
                          })}
                      </>
                    )}

                    {/* Right-Hand Annual Totals Column */}
                    <g transform={`translate(${rightBarOffset}, ${y + 1})`}>
                      <rect
                        x={0}
                        y={0}
                        width={annualBarMaxW}
                        height={ROW_H - 4}
                        fill="#1C1A22"
                        rx={2}
                      />
                      <rect
                        x={0}
                        y={0}
                        width={barWidth}
                        height={ROW_H - 4}
                        fill={isTransferredYear ? "#E5A93C" : "#E04F33"}
                        opacity={0.85}
                        rx={2}
                      />
                      <text
                        x={annualBarMaxW + 8}
                        y={ROW_H - 9}
                        fontSize={10}
                        fill="#F4F3F6"
                        fontFamily="JetBrains Mono, monospace"
                        fontWeight="500"
                      >
                        {annualSum.toLocaleString()}
                      </text>
                    </g>
                  </g>
                );
              })}

              {/* Provenance strip at bottom */}
              {years.map((year, yi) => {
                const y = TOP + years.length * ROW_H + 8 + yi * 4;
                const rowCells = calendar.cells.filter((c) => c.year === year);
                return (
                  <g key={`prov-${year}`}>
                    {rowCells.map((c) => (
                      <rect
                        key={c.date}
                        x={GUTTER + (c.doy - 1) * cellW}
                        y={y}
                        width={Math.max(1.2, cellW - 0.4)}
                        height={3}
                        fill={PROVENANCE_COLOR[c.provenance]}
                        opacity={0.85}
                      />
                    ))}
                  </g>
                );
              })}
              <text x={GUTTER - 8} y={TOP + years.length * ROW_H + 16} fontSize={9} fill="#686472" textAnchor="end" fontFamily="JetBrains Mono">
                ERA
              </text>
            </svg>

            {/* Footnote bar */}
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[11px] text-muted border-t border-basalt-300 pt-3">
              {(Object.keys(PROVENANCE_COLOR) as (keyof typeof PROVENANCE_COLOR)[]).map((p) => (
                <span key={p} className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-3 rounded-sm" style={{ background: PROVENANCE_COLOR[p] }} />
                  <span className="text-pewter">{PROVENANCE_LABEL[p]}</span>
                </span>
              ))}
              <span className="flex items-center gap-1">▲ <span className="text-pewter">Fire-season onset</span></span>
              <span className="flex items-center gap-1">▼ <span className="text-pewter">Season end</span></span>
              <span className="text-pewter">
                {hasWeekly ? "2000–2011: ISO weeks (MODIS transferred); 2012+: Daily calibrated VIIRS." : ""}
              </span>
            </div>
          </div>

          {/* Interactive Cell Inspector Panel (Right Drawer) */}
          {selectedCell && (
            <div className="w-80 shrink-0 border-l border-basalt-300 bg-basalt-200/90 backdrop-blur p-4 flex flex-col justify-between overflow-y-auto rounded-r-xl">
              <div>
                <div className="flex items-center justify-between border-b border-basalt-300 pb-2 mb-3">
                  <div>
                    <div className="text-[10px] font-bold tracking-widest text-muted uppercase font-mono">
                      Selected Day
                    </div>
                    <div className="font-mono text-sm font-bold text-terra">
                      {prettyDate(selectedCell.date)}
                    </div>
                  </div>
                  <button
                    onClick={() => setSelected(null)}
                    className="rounded-md border border-basalt-400 p-1 text-muted hover:text-alabaster transition"
                    aria-label="Close inspector"
                  >
                    ✕
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-2.5 text-xs">
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">Harmonized Fires</div>
                    <div className="font-mono text-base font-bold text-alabaster">{fmtNum(selectedCell.n_harmonized)}</div>
                  </div>
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">FRP (Harmonized)</div>
                    <div className="font-mono text-base font-bold text-amber">{fmtMw(selectedCell.frp_harmonized_mw)}</div>
                  </div>
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">MODIS (1 km)</div>
                    <div className="font-mono text-sm font-semibold text-amber">{fmtInt(selectedCell.n_modis)}</div>
                  </div>
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">VIIRS (375 m)</div>
                    <div className="font-mono text-sm font-semibold text-terra">{fmtInt(selectedCell.n_viirs)}</div>
                  </div>
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">Static Flares</div>
                    <div className="font-mono text-sm font-semibold text-pewter">{fmtInt(selectedCell.n_static)}</div>
                  </div>
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">Static Share</div>
                    <div className="font-mono text-sm font-semibold text-pewter">
                      {selectedCell.static_share == null ? "—" : `${(selectedCell.static_share * 100).toFixed(0)}%`}
                    </div>
                  </div>
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">LOYO Percentile</div>
                    <div className="font-mono text-sm font-semibold text-alabaster">
                      {selectedCell.pct_n == null ? "—" : `${selectedCell.pct_n.toFixed(0)}%`}
                    </div>
                  </div>
                  <div className="rounded-lg bg-basalt-100 p-2 border border-basalt-300">
                    <div className="text-[9px] text-muted uppercase font-mono">Data Era</div>
                    <div className="text-[10px] font-semibold text-jade truncate">{PROVENANCE_LABEL[selectedCell.provenance]}</div>
                  </div>
                </div>

                {selectedCell.flag_high && (
                  <div className="mt-3 rounded-lg border border-coral/30 bg-coral/10 p-2.5 text-[11px] text-coral leading-snug font-medium">
                    🔥 <strong>Unusually active:</strong> Detection count reaches or exceeds the 95th percentile for this day of year.
                  </div>
                )}
                {selectedCell.flag_low && (
                  <div className="mt-3 rounded-lg border border-teal/30 bg-teal/10 p-2.5 text-[11px] text-teal leading-snug font-medium">
                    ❄️ <strong>Unusually quiet:</strong> Zero fire detections where historical baseline expects activity.
                  </div>
                )}
              </div>

              {/* Jump to Map action button */}
              <div className="pt-4 border-t border-basalt-300">
                <button
                  onClick={() => {
                    setCursorDate(selectedCell.date);
                    setView("map");
                  }}
                  className="w-full rounded-xl bg-terra py-2.5 px-4 font-display text-xs font-bold text-alabaster shadow-md transition hover:bg-ember-400 flex items-center justify-center gap-2"
                >
                  <span>Jump to Map on This Date</span>
                  <span>→</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Tooltips */}
      {hover?.kind === "day" && <DayTooltip cell={hover.cell} x={hover.x} y={hover.y} />}
      {hover?.kind === "week" && <WeekTooltip week={hover.week} x={hover.x} y={hover.y} />}
      {hover?.kind === "month" && <MonthTooltip {...hover} />}
    </div>
  );
}

function MonthTooltip({
  year,
  month,
  total,
  frp,
  deltaPct,
  isEstimated,
  x,
  y,
}: {
  year: number;
  month: number;
  total: number;
  frp: number;
  deltaPct: number;
  isEstimated: boolean;
  x: number;
  y: number;
}) {
  return (
    <div className="ember-tooltip" style={{ left: x + 14, top: y + 14 }}>
      <div className="font-mono text-[11px] font-bold text-alabaster flex items-center justify-between gap-3">
        <span>{MONTHS[month - 1]} {year}</span>
        <span
          className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${
            deltaPct >= 10
              ? "bg-coral/20 text-coral"
              : deltaPct <= -10
              ? "bg-jade/20 text-jade"
              : "bg-basalt-300 text-pewter"
          }`}
        >
          {deltaPct >= 0 ? "+" : ""}{deltaPct.toFixed(1)}% vs. Median
        </span>
      </div>
      <div className="mt-1 grid grid-cols-[auto_auto] gap-x-3 text-pewter font-sans">
        <span className="text-muted">Harmonized Total</span>
        <span className="text-right font-mono text-terra font-semibold">{fmtInt(total)} fires</span>
        <span className="text-muted">Mean FRP</span>
        <span className="text-right font-mono text-amber">{fmtMw(frp)}</span>
        <span className="text-muted">Data Era</span>
        <span className="text-right text-[10px] text-pewter">{isEstimated ? "MODIS Transferred" : "VIIRS Native"}</span>
      </div>
    </div>
  );
}

function dayOfYear(iso: string): number {
  const d = new Date(iso + "T00:00:00Z");
  const start = Date.UTC(d.getUTCFullYear(), 0, 1);
  return Math.floor((d.getTime() - start) / 86_400_000) + 1;
}

function DayTooltip({ cell, x, y }: { cell: CalendarCell; x: number; y: number }) {
  return (
    <div className="ember-tooltip" style={{ left: x + 14, top: y + 14 }}>
      <div className="font-mono text-[11px] font-bold text-terra">{cell.date}</div>
      <div className="mt-1 grid grid-cols-[auto_auto] gap-x-3 text-pewter font-sans">
        <span style={{ color: SENSOR_COLOR.MODIS }}>MODIS</span>
        <span className="text-right font-mono">{cell.n_modis} · {fmtNum(cell.frp_modis_mw)} MW</span>
        <span style={{ color: SENSOR_COLOR.VIIRS }}>VIIRS</span>
        <span className="text-right font-mono">{cell.n_viirs} · {fmtNum(cell.frp_viirs_mw)} MW</span>
        <span className="text-muted">Harmonized</span>
        <span className="text-right font-mono text-alabaster font-semibold">{fmtNum(cell.n_harmonized)}</span>
        <span className="text-muted">FRP (Harm.)</span>
        <span className="text-right font-mono text-alabaster">{fmtNum(cell.frp_harmonized_mw)} MW</span>
        <span className="text-muted">LOYO Pct</span>
        <span className="text-right font-mono">
          {cell.pct_n == null ? "—" : `${cell.pct_n.toFixed(0)}%`}
        </span>
        <span className="text-muted">Era</span>
        <span className="text-right text-[10px]">{PROVENANCE_LABEL[cell.provenance]}</span>
      </div>
      {cell.flag_high && (
        <div className="mt-1 font-semibold text-coral text-[10px]">▲ Unusually active vs historical baseline</div>
      )}
      {cell.flag_low && (
        <div className="mt-1 font-semibold text-teal text-[10px]">● Unusually quiet vs historical baseline</div>
      )}
    </div>
  );
}

function WeekTooltip({ week, x, y }: { week: TransferredWeek; x: number; y: number }) {
  return (
    <div className="ember-tooltip" style={{ left: x + 14, top: y + 14 }}>
      <div className="font-mono text-[11px] font-bold text-amber">
        {week.start_date} → {week.end_date} (ISO week {week.iso_week})
      </div>
      <div className="mt-1 grid grid-cols-[auto_auto] gap-x-3 text-pewter font-sans">
        <span className="text-muted">Weekly Total (Est.)</span>
        <span className="text-right font-mono text-alabaster font-semibold">{fmtNum(week.n_harmonized)}</span>
        {week.n_harmonized_lo !== null && week.n_harmonized_hi !== null && (
          <>
            <span className="text-muted">95% Interval</span>
            <span className="text-right font-mono text-muted">
              {fmtNum(week.n_harmonized_lo)}–{fmtNum(week.n_harmonized_hi)}
            </span>
          </>
        )}
        <span className="text-muted">Days with data</span>
        <span className="text-right font-mono">{week.n_days_with_data}/7</span>
        <span className="text-muted">LOYO Pct</span>
        <span className="text-right font-mono">{week.pct_n === null ? "—" : `${week.pct_n.toFixed(0)}%`}</span>
      </div>
      <div className="mt-1 text-[9px] text-muted">
        2000–2011 transferred era: scaled MODIS detections to VIIRS equivalence.
      </div>
    </div>
  );
}

function Empty({ message, spinner }: { message: string; spinner?: boolean }) {
  return (
    <div className="flex h-full items-center justify-center bg-basalt">
      <div className="flex items-center gap-3 text-sm text-pewter font-display">
        {spinner && (
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-basalt-400 border-t-terra" />
        )}
        {message}
      </div>
    </div>
  );
}
