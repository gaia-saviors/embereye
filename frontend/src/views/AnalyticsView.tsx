import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";

import { api } from "../api/client";
import type {
  ClimatologyResponse,
  EpisodeOut,
  HeadlineResponse,
  SeasonOut,
  SummaryResponse,
  TimeseriesPoint,
} from "../api/types";
import { useStore } from "../store";
import { SENSOR_COLOR } from "../lib/colors";
import { addDays, fmtInt, fmtNum } from "../lib/format";

const STATIC_COLOR = "#A78BFA"; // persistent-source layer
const NIGHT_COLOR = "#475E77";  // non-static night layer
const BAND_COLOR = "rgba(224, 79, 51, 0.16)";
const SEASON_ESTIMATED = "#E5A93C";
const SEASON_MEASURED = "#E04F33";
const WARN_TEXT = "#E5A93C";

export default function AnalyticsView() {
  const aoiId = useStore((s) => s.aoiId);
  const rangeStart = useStore((s) => s.rangeStart);
  const rangeEnd = useStore((s) => s.rangeEnd);
  const setRange = useStore((s) => s.setRange);
  const dataRevision = useStore((s) => s.dataRevision);
  const filters = useStore((s) => s.filters);
  const includeLayers = useStore((s) => s.includeLayers);
  const setIncludeLayers = useStore((s) => s.setIncludeLayers);
  const setCursorDate = useStore((s) => s.setCursorDate);
  const setView = useStore((s) => s.setView);

  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [headline, setHeadline] = useState<HeadlineResponse | null>(null);
  const [series, setSeries] = useState<TimeseriesPoint[] | null>(null);
  const [res, setRes] = useState<"day" | "week" | "month">("month");
  const [error, setError] = useState<string | null>(null);

  const [clim, setClim] = useState<ClimatologyResponse | null>(null);
  const [seasons, setSeasons] = useState<SeasonOut[] | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeOut[] | null>(null);

  useEffect(() => {
    if (!aoiId) return;
    let alive = true;
    setClim(null);
    setSeasons(null);
    setEpisodes(null);
    setError(null);
    Promise.all([
      api.climatology(aoiId),
      api.seasons(aoiId),
      api.episodes(aoiId, false, 150),
    ])
      .then(([c, se, ep]) => {
        if (!alive) return;
        setClim(c);
        setSeasons(se);
        setEpisodes(ep);
      })
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [aoiId, dataRevision]);

  useEffect(() => {
    if (!aoiId) return;
    let alive = true;
    setError(null);
    setSummary(null);
    setHeadline(null);
    setSeries(null);
    Promise.all([
      api.summary(aoiId, undefined, undefined, includeLayers),
      api.headline(aoiId),
      api.timeseries(aoiId, rangeStart, rangeEnd, res),
    ])
      .then(([s, hl, ts]) => {
        if (!alive) return;
        setSummary(s);
        setHeadline(hl);
        setSeries(ts);
      })
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [aoiId, rangeStart, rangeEnd, res, includeLayers, dataRevision]);

  if (!aoiId) return <Centered>Select an area of interest.</Centered>;
  if (error) {
    const isNoData = error.includes("404") || error.includes("no");
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 text-center bg-basalt">
        <div className="max-w-md rounded-2xl border border-basalt-300 bg-basalt-100 p-8 shadow-2xl">
          <div className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full bg-terra/10 text-2xl text-terra">
            📊
          </div>
          <h3 className="mb-2 font-display text-base font-bold text-alabaster">
            {isNoData ? "Analytics Not Available for this AOI" : "Analytics Error"}
          </h3>
          <p className="mb-5 text-xs leading-relaxed text-pewter">
            {isNoData
              ? "The full 26-year NASA FIRMS analytics archive is active for Nile Delta, Egypt. Other preset archives can be populated via demo seeder or CSV archives."
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
  if (!summary || !series || !clim || !seasons || !episodes || !headline) {
    return (
      <Centered spinner>
        <div>
          <div>Loading multi-decadal analytics…</div>
          <p className="mt-1 max-w-sm text-xs text-muted">
            First load reads several large archive summaries and may take a few seconds.
          </p>
        </div>
      </Centered>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-basalt p-5 flex flex-col gap-5">
      <div role="note" className="rounded-xl border border-amber/30 bg-amber/5 px-4 py-2.5 text-[11px] leading-relaxed text-pewter">
        <span className="font-semibold text-amber">Scope:</span> the date range applies to this time-series chart only. Ribbon filters apply to Map and Calendar; headline KPIs, climatology, seasons, and episode rankings use the stored aggregate series.
      </div>
      {/* ── Headline Analytic Banner ── */}
      <section className="rounded-2xl border border-terra/40 bg-basalt-100 p-4 shadow-md">
        {headline.complete_month ? (
          <>
            <div className="flex flex-wrap items-center justify-between mb-3 border-b border-basalt-300 pb-2">
              <div className="flex items-center gap-2">
                <h3 className="font-display text-xs font-bold tracking-wide text-alabaster uppercase">
                  Last Complete Month — {monthLabel(headline.complete_month)} vs Historical Baseline
                </h3>
                <span className="font-mono text-[10px] text-muted">
                  · Comparable subset: Day, Non-static
                </span>
              </div>
              <button
                type="button"
                onClick={() => setIncludeLayers(!includeLayers)}
                aria-pressed={includeLayers}
                className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-medium transition border ${
                  includeLayers
                    ? "bg-terra/15 border-terra text-alabaster shadow-sm"
                    : "bg-basalt-200 border-basalt-300 text-muted hover:border-basalt-400 hover:text-pewter"
                }`}
                title="Include permanent heat (gas flares) & night detections in totals"
              >
                <span className={`h-1.5 w-1.5 rounded-full ${includeLayers ? "bg-terra" : "bg-muted"}`} />
                <span>Include permanent heat &amp; night layer</span>
              </button>
            </div>
            <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
              <div>
                <div className="text-[10px] text-muted uppercase font-mono">
                  {monthLabel(headline.complete_month)} Total
                </div>
                <div className="font-mono text-2xl font-bold text-terra">
                  {fmtNum(headline.complete_total)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-muted uppercase font-mono">
                  Historical Median
                  {headline.complete_years.length > 0 && (
                    <span className="text-muted lowercase">
                      {" "}({headline.complete_years[0]}–{headline.complete_years[headline.complete_years.length - 1]}, n={headline.complete_years.length})
                    </span>
                  )}
                </div>
                <div className="font-mono text-2xl font-bold text-alabaster">
                  {headline.complete_median != null ? fmtNum(headline.complete_median) : "—"}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-muted uppercase font-mono">Interquartile Range (IQR)</div>
                <div className="font-mono text-sm font-semibold text-pewter">
                  {headline.complete_p25 != null
                    ? `${fmtNum(headline.complete_p25)} – ${fmtNum(headline.complete_p75)}`
                    : "—"}
                </div>
              </div>
              <div
                className="rounded-lg border px-3 py-1.5 text-xs font-bold font-display"
                style={{
                  borderColor:
                    headline.complete_percentile_rank != null && headline.complete_percentile_rank >= 95
                      ? "#F43F5E"
                      : headline.complete_percentile_rank != null && headline.complete_percentile_rank <= 5
                        ? "#2DD4BF"
                        : "#383444",
                  color:
                    headline.complete_percentile_rank != null && headline.complete_percentile_rank >= 95
                      ? "#F43F5E"
                      : headline.complete_percentile_rank != null && headline.complete_percentile_rank <= 5
                        ? "#2DD4BF"
                        : "#A09CA8",
                  backgroundColor:
                    headline.complete_percentile_rank != null && headline.complete_percentile_rank >= 95
                      ? "rgba(244, 63, 94, 0.1)"
                      : headline.complete_percentile_rank != null && headline.complete_percentile_rank <= 5
                        ? "rgba(45, 212, 191, 0.1)"
                        : "transparent",
                }}
              >
                {headline.complete_percentile_rank != null
                  ? `p${headline.complete_percentile_rank.toFixed(0)} · ${headline.complete_verdict}`
                  : headline.complete_verdict}
              </div>
            </div>
            <div className="mt-2 text-xs text-pewter leading-relaxed">{headline.complete_note}</div>
          </>
        ) : (
          <>
            <h3 className="mb-2 font-display text-xs font-bold tracking-wide text-alabaster uppercase">
              Headline — {monthLabel(headline.month)} vs Historical Median
            </h3>
            <div className="text-xs text-pewter">{headline.verdict}</div>
          </>
        )}
        {headline.current_partial && (
          <div className="mt-2.5 border-t border-basalt-300 pt-2 text-xs text-muted">
            <span className="font-semibold text-alabaster">
              {monthLabel(headline.month)} (in progress, {headline.days_in_month} day
              {headline.days_in_month === 1 ? "" : "s"} in):
            </span>{" "}
            {fmtNum(headline.current_total)} detections recorded so far
            {headline.days_in_month < headline.min_days_for_verdict && (
              <span> · pending minimum window: {headline.min_days_for_verdict} days</span>
            )}
          </div>
        )}
      </section>

      {/* ── Summary Metric Cards (Exact 6-column single row) ── */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Card
          label={includeLayers ? "Detections (incl. layers)" : "Detections (comparable)"}
          value={fmtNum(summary.total_detections_harmonized)}
          accent
        />
        <Card label="Radiative Power" value={`${fmtNum(summary.total_frp_harmonized_mw)} MW`} accent />
        <Card
          label="Static & Night Share"
          value={`${summary.static_share != null ? `${(summary.static_share * 100).toFixed(1)}%` : "0.0%"} · ${fmtInt(summary.n_static_total)} / ${fmtInt(summary.n_night_total)}`}
          color={STATIC_COLOR}
          tip={`Static Share: ${summary.static_share != null ? `${(summary.static_share * 100).toFixed(2)}%` : "0.0%"}\nFlares: ${summary.n_static_total.toLocaleString()} · Night: ${summary.n_night_total.toLocaleString()}`}
        />
        <Card label="Active Days" value={fmtInt(summary.active_days)} />
        <Card
          label="Active / Quiet Days"
          value={`${fmtInt(summary.n_anomaly_high)} / ${fmtInt(summary.n_anomaly_low)}`}
          tip="Leave-one-year-out empirical percentiles (p >= 95 / p <= 5)."
          color={summary.n_anomaly_high > 0 ? "#F43F5E" : undefined}
        />
        <Card
          label="Season Length (latest)"
          value={
            summary.season_current?.season_length_days != null
              ? `${fmtInt(summary.season_current.season_length_days)}d${
                  summary.season_current.is_partial ? "*" : ""
                }`
              : "—"
          }
          tip={summary.season_length_definition ?? ""}
        />
      </div>

      {/* ── Timeseries Section ── */}
      <section className="rounded-2xl border border-basalt-300 bg-basalt-100 p-4 shadow-md">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border-b border-basalt-300 pb-2">
          <div className="flex items-center gap-3">
            <h3 className="font-display text-xs font-bold tracking-wide text-alabaster uppercase">
              Harmonized Multi-Decadal Time Series
            </h3>
            <div className="seg-pill">
              {(["day", "week", "month"] as const).map((r) => (
                <button
                  key={r}
                  onClick={() => setRes(r)}
                  aria-pressed={res === r}
                  className={res === r ? "active" : ""}
                >
                  {r.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
          <BandBasisNote points={series} res={res} />
        </div>
        <TimeseriesChart points={series} res={res} />
      </section>

      {/* ── Climatology & Seasons Grid ── */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {/* Day of Year Climatology */}
        <section className="rounded-2xl border border-basalt-300 bg-basalt-100 p-4 shadow-md">
          <h3 className="mb-3 font-display text-xs font-bold tracking-wide text-alabaster uppercase border-b border-basalt-300 pb-2">
            Day-of-Year Climatology ({clim.baseline.start_year}–{clim.baseline.end_year} Baseline)
          </h3>
          <ClimatologyChart clim={clim} />
        </section>

        {/* Fire Seasons */}
        <section className="rounded-2xl border border-basalt-300 bg-basalt-100 p-4 shadow-md">
          <h3 className="mb-3 font-display text-xs font-bold tracking-wide text-alabaster uppercase border-b border-basalt-300 pb-2">
            Annual Fire Seasons (* = Censored Partial Year)
          </h3>
          <SeasonsChart seasons={seasons} />
          <div className="mt-2 text-[10px] text-muted">
            Transferred era bars (2000–2011) reflect scaled MODIS estimates; VIIRS era bars (2012+) reflect direct observations.
          </div>
          <div className="mt-3 max-h-36 overflow-auto text-xs">
            <table className="min-w-[540px] w-full text-left">
              <thead className="sticky top-0 bg-basalt-100 text-muted font-mono text-[10px]">
                <tr>
                  <th className="py-1 pr-2">Year</th>
                  <th className="py-1 pr-2">Onset</th>
                  <th className="py-1 pr-2">End</th>
                  <th className="py-1 pr-2">Length</th>
                  <th className="py-1 pr-2">Peak Day</th>
                  <th className="py-1">Active</th>
                </tr>
              </thead>
              <tbody className="font-mono text-[11px]">
                {[...seasons].reverse().map((s) => (
                  <tr key={s.year} className={`border-t border-basalt-300 ${s.is_partial ? "opacity-60" : ""}`}>
                    <td className="py-1 pr-2 text-alabaster">
                      {s.year}
                      {s.is_partial && <span className="ml-0.5 text-terra">*</span>}
                    </td>
                    <td className="py-1 pr-2 text-pewter">{s.onset_date ?? "—"}</td>
                    <td className="py-1 pr-2 text-pewter">{s.end_date ?? "—"}</td>
                    <td className="py-1 pr-2 text-alabaster">{s.season_length_days ?? "—"}</td>
                    <td className="py-1 pr-2 text-pewter">{s.peak_date ?? "—"}</td>
                    <td className="py-1 text-pewter">{s.active_days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* ── Significant Fire Episodes ── */}
      <section className="rounded-2xl border border-basalt-300 bg-basalt-100 p-4 shadow-md">
        <h3 className="mb-3 font-display text-xs font-bold tracking-wide text-alabaster uppercase border-b border-basalt-300 pb-2">
          Significant Burning Episodes — Ranked by Severity ({episodes.length} shown)
        </h3>
        <div className="max-h-72 overflow-auto">
          <table className="min-w-[760px] w-full text-left text-xs">
            <thead className="sticky top-0 bg-basalt-100 text-muted font-mono text-[10px]">
              <tr>
                <th className="py-1.5 pr-3">Episode</th>
                <th className="py-1.5 pr-3">Start Date</th>
                <th className="py-1.5 pr-3">Duration</th>
                <th className="py-1.5 pr-3">Detections</th>
                <th className="py-1.5 pr-3">Peak FRP</th>
                <th className="py-1.5 pr-3">Total FRP</th>
                <th className="py-1.5 pr-3">Mix (M/V)</th>
                <th className="py-1.5">Action</th>
              </tr>
            </thead>
            <tbody className="font-mono text-[11px]">
              {episodes.map((e) => (
                <tr key={e.id} className="border-t border-basalt-300 hover:bg-basalt-200/50 transition-colors">
                  <td className="py-2 pr-3 font-sans font-semibold text-alabaster">
                    {e.is_critical && <span className="mr-1 text-coral">◆</span>}
                    {e.label}
                  </td>
                  <td className="py-2 pr-3 text-pewter">{e.start_date}</td>
                  <td className="py-2 pr-3 text-alabaster">{e.duration_days}d</td>
                  <td className="py-2 pr-3 text-terra font-bold">{fmtInt(e.n_detections)}</td>
                  <td className="py-2 pr-3 text-amber">{fmtNum(e.peak_frp_mw)} MW</td>
                  <td className="py-2 pr-3 text-pewter">{fmtNum(e.total_frp_mw)} MW</td>
                  <td className="py-2 pr-3 text-muted">
                    {e.sensor_mix.MODIS ?? 0}/{e.sensor_mix.VIIRS ?? 0}
                  </td>
                  <td className="py-2 text-right">
                    <button
                      onClick={() => {
                        setRange(e.start_date, addDays(e.start_date, Math.max(0, e.duration_days - 1)));
                        setCursorDate(e.start_date);
                        setView("map");
                      }}
                      aria-label={`View episode ${e.label} on the map`}
                      className="rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1 text-[10px] font-bold text-pewter hover:border-terra hover:text-terra transition-colors"
                    >
                      VIEW MAP →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Direct Data Exports ── */}
      <section className="rounded-2xl border border-basalt-300 bg-basalt-100 p-4 shadow-md flex flex-wrap items-center gap-2">
        <h3 className="w-full font-display text-xs font-bold tracking-wide text-alabaster uppercase mb-1">
          Direct Data &amp; GIS Exports
        </h3>
        {[
          ["harmonized", "csv"],
          ["daily", "csv"],
          ["hotspots", "csv"],
          ["hotspots", "geojson"],
          ["episodes", "csv"],
          ["episodes", "geojson"],
        ].map(([level, format]) => (
          <a
            key={`${level}-${format}`}
            href={api.exportUrl({
              aoi: aoiId, level, format, start: rangeStart, end: rangeEnd,
              ...(level === "hotspots" ? {
                sensor: filters.sensor,
                confidence: filters.confidence,
                daynight: filters.daynight,
                min_frp: filters.minFrp ?? undefined,
                hide_static: filters.hideStaticSources,
              } : {}),
            })}
            className="rounded-lg border border-basalt-300 bg-basalt-200 px-3 py-1.5 font-mono text-[11px] font-semibold text-pewter hover:border-terra hover:text-terra transition-colors"
          >
            {level.toUpperCase()} · {format.toUpperCase()}
          </a>
        ))}
      </section>
    </div>
  );
}

function BandBasisNote({ points, res }: { points: TimeseriesPoint[]; res: string }) {
  const basis = points.find((p) => p.interval_basis)?.interval_basis ?? null;
  const stale = basis != null && basis.includes("rebuild");
  if (stale) {
    return (
      <span
        className="rounded-lg border border-amber/40 bg-amber/10 px-2.5 py-1 text-[10px] font-mono"
        style={{ color: WARN_TEXT }}
      >
        Uncertainty band computed on annual basis — matched for {res}.
      </span>
    );
  }
  return (
    <span className="text-[10px] text-muted font-mono">
      Shaded region: 95% LOYO prediction interval ({res} aggregation)
    </span>
  );
}

function monthLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleString("en", { month: "short" }).toUpperCase() + " " + d.getUTCFullYear();
}

function TimeseriesChart({ points, res }: { points: TimeseriesPoint[]; res: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, "dark");
    const loHi: [string, number, number][] = points
      .filter((p) => p.n_harmonized_lo != null && p.n_harmonized_hi != null
        && p.n_harmonized_hi - p.n_harmonized_lo > 0)
      .map((p) => [p.date, p.n_harmonized_lo as number, p.n_harmonized_hi as number]);

    chart.setOption({
      backgroundColor: "transparent",
      animation: false,
      toolbox: {
        feature: {
          saveAsImage: { title: "Save Chart Image (PNG)", pixelRatio: 2, backgroundColor: "#141318" },
        },
        right: 16,
        top: 0,
        iconStyle: { borderColor: "#A09CA8" },
      },
      grid: { left: 48, right: 16, top: 32, bottom: 46 },
      legend: {
        top: 0,
        textStyle: { fontSize: 11, color: "#A09CA8", fontFamily: "Inter" },
        itemWidth: 12,
        itemHeight: 8,
        data: [
          "MODIS raw (1 km)",
          "VIIRS raw (ref. platform)",
          "Static layer",
          "Night layer",
          "Harmonized (VIIRS-equiv)",
          "95% uncertainty band",
        ],
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#141318",
        borderColor: "#25232C",
        textStyle: { fontSize: 11, color: "#F4F3F6" },
      },
      dataZoom: [
        { type: "inside" },
        {
          type: "slider",
          height: 16,
          bottom: 6,
          borderColor: "#25232C",
          fillerColor: "rgba(224, 79, 51, 0.2)",
          handleStyle: { color: "#E04F33" },
        },
      ],
      xAxis: {
        type: "time",
        axisLabel: { fontSize: 10, color: "#A09CA8", fontFamily: "JetBrains Mono" },
        axisLine: { lineStyle: { color: "#25232C" } },
      },
      yAxis: [
        {
          type: "value",
          name: "detections",
          min: 0,
          nameTextStyle: { fontSize: 10, color: "#A09CA8" },
          axisLabel: { fontSize: 10, color: "#A09CA8", fontFamily: "JetBrains Mono" },
          splitLine: { lineStyle: { color: "#25232C" } },
        },
      ],
      series: [
        ...(loHi.length
          ? [{
              name: "95% uncertainty band",
              type: "line",
              data: loHi.map((d) => [d[0], d[2] - d[1] >= 0 ? d[1] : d[2]]),
              lineStyle: { opacity: 0 },
              symbol: "none",
              areaStyle: { color: "transparent" },
              stack: "pi-band",
              silent: true,
            },
            {
              name: "95% uncertainty band (span)",
              type: "line",
              data: loHi.map((d) => [d[0], Math.abs(d[2] - d[1])]),
              lineStyle: { opacity: 0 },
              symbol: "none",
              stack: "pi-band",
              areaStyle: { color: BAND_COLOR },
              silent: true,
            }]
          : []),
        {
          name: "MODIS raw (1 km)",
          type: "bar",
          stack: "raw",
          itemStyle: { color: SENSOR_COLOR.MODIS, opacity: 0.8 },
          data: points.map((p) => [p.date, p.n_modis]),
          barMaxWidth: res === "day" ? 3 : 9,
        },
        {
          name: "VIIRS raw (ref. platform)",
          type: "bar",
          stack: "raw",
          itemStyle: { color: SENSOR_COLOR.VIIRS, opacity: 0.8 },
          data: points.map((p) => [p.date, p.n_viirs]),
          barMaxWidth: res === "day" ? 3 : 9,
        },
        {
          name: "Static layer",
          type: "bar",
          stack: "layers",
          itemStyle: { color: STATIC_COLOR, opacity: 0.8 },
          data: points.map((p) => [p.date, p.n_static]),
          barMaxWidth: res === "day" ? 3 : 9,
        },
        {
          name: "Night layer",
          type: "bar",
          stack: "layers",
          itemStyle: { color: NIGHT_COLOR, opacity: 0.8 },
          data: points.map((p) => [p.date, p.n_night]),
          barMaxWidth: res === "day" ? 3 : 9,
        },
        {
          name: "Harmonized (VIIRS-equiv)",
          type: "line",
          symbol: "none",
          lineStyle: { color: "#E04F33", width: 2 },
          data: points.map((p) => [p.date, Math.round(p.n_harmonized * 100) / 100]),
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [points, res]);
  return <div ref={ref} className="h-64" />;
}

function ClimatologyChart({ clim }: { clim: ClimatologyResponse }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, "dark");
    const sortedDoy = [...clim.doy].sort((a, b) => a.doy - b.doy);
    const base = sortedDoy.map((d) => Math.min(d.p10_n, d.p90_n));
    const span = sortedDoy.map((d) => Math.max(0, d.p90_n - Math.min(d.p10_n, d.p90_n)));

    chart.setOption({
      backgroundColor: "transparent",
      animation: false,
      toolbox: {
        feature: {
          saveAsImage: { title: "Save Climatology Image (PNG)", pixelRatio: 2, backgroundColor: "#141318" },
        },
        right: 12,
        top: 0,
        iconStyle: { borderColor: "#A09CA8" },
      },
      grid: { left: 48, right: 16, top: 44, bottom: 26 },
      legend: { show: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#141318",
        borderColor: "#25232C",
        textStyle: { fontSize: 11, color: "#F4F3F6" },
        formatter: (items: any) => {
          if (!items || !items.length) return "";
          const idx = items[0].dataIndex;
          const pt = sortedDoy[idx];
          if (!pt) return "";
          return (
            `<div style="font-family:JetBrains Mono,monospace;font-size:11px;">` +
            `<b>Day of Year ${pt.doy}</b><br/>` +
            `<span style="color:#E04F33">Mean: <b>${pt.mean_n.toFixed(1)}</b></span> fires/day<br/>` +
            `<span style="color:#A09CA8">p10–p90 band: <b>${pt.p10_n.toFixed(1)} – ${pt.p90_n.toFixed(1)}</b></span>` +
            `</div>`
          );
        },
      },
      xAxis: {
        type: "category",
        data: sortedDoy.map((d) => String(d.doy)),
        name: "day of year",
        nameTextStyle: { fontSize: 10, color: "#A09CA8" },
        axisLabel: { interval: 29, fontSize: 10, color: "#A09CA8", fontFamily: "JetBrains Mono" },
        axisTick: { alignWithLabel: true },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: 0,
        name: "detections/day",
        nameTextStyle: { fontSize: 10, color: "#A09CA8" },
        axisLabel: { fontSize: 10, color: "#A09CA8", fontFamily: "JetBrains Mono" },
        splitLine: { lineStyle: { color: "#25232C" } },
      },
      series: [
        {
          name: "band-base",
          type: "line",
          data: base,
          lineStyle: { opacity: 0 },
          symbol: "none",
          stack: "doy-band",
          areaStyle: { color: "transparent" },
          silent: true,
        },
        {
          name: "p10-p90 band",
          type: "line",
          data: span,
          lineStyle: { opacity: 0 },
          symbol: "none",
          stack: "doy-band",
          areaStyle: { color: BAND_COLOR },
          silent: true,
        },
        {
          name: "baseline mean",
          type: "line",
          symbol: "none",
          lineStyle: { color: "#E04F33", width: 2 },
          data: sortedDoy.map((d) => d.mean_n),
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [clim]);
  return <div ref={ref} className="h-56" />;
}

function SeasonsChart({ seasons }: { seasons: SeasonOut[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, "dark");
    const years = seasons.map((s) => s.year);
    const seamIdx = years.findIndex((y) => y >= 2012);

    chart.setOption({
      backgroundColor: "transparent",
      animation: false,
      toolbox: {
        feature: {
          saveAsImage: { title: "Save Seasons Chart (PNG)", pixelRatio: 2, backgroundColor: "#141318" },
        },
        right: 12,
        top: 0,
        iconStyle: { borderColor: "#A09CA8" },
      },
      grid: { left: 40, right: 12, top: 26, bottom: 40 },
      legend: { top: 0, textStyle: { fontSize: 10, color: "#A09CA8" }, itemWidth: 12, itemHeight: 8 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#141318",
        borderColor: "#25232C",
        textStyle: { fontSize: 11, color: "#F4F3F6" },
      },
      xAxis: {
        type: "category",
        data: seasons.map((s) => s.year + (s.is_partial ? "*" : "")),
        axisLabel: { fontSize: 9, color: "#A09CA8", rotate: 45, fontFamily: "JetBrains Mono" },
        axisLine: { lineStyle: { color: "#25232C" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        name: "days",
        nameTextStyle: { fontSize: 10, color: "#A09CA8" },
        axisLabel: { fontSize: 10, color: "#A09CA8", fontFamily: "JetBrains Mono" },
        splitLine: { lineStyle: { color: "#25232C" } },
      },
      series: [
        {
          name: "transferred era (estimated)",
          type: "bar",
          stack: "season",
          itemStyle: {
            color: SEASON_ESTIMATED,
            decal: { symbol: "rect", rotation: Math.PI / 6, dashArray: [2, 2] },
          },
          data: seasons.map((s) =>
            s.year < 2012 ? (s.season_length_days ?? 0) : 0,
          ),
          barMaxWidth: 14,
          markLine: seamIdx > 0
            ? {
                silent: true,
                symbol: "none",
                label: { formatter: "VIIRS era →", fontSize: 9, color: "#A09CA8", position: "insideEndTop" },
                lineStyle: { color: "#A09CA8", type: "dashed", width: 1 },
                data: [{ xAxis: seamIdx - 0.5 }],
              }
            : undefined,
        },
        {
          name: "VIIRS era (measured)",
          type: "bar",
          stack: "season",
          itemStyle: { color: SEASON_MEASURED },
          data: seasons.map((s) =>
            s.year >= 2012 ? (s.season_length_days ?? 0) : 0,
          ),
          barMaxWidth: 14,
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [seasons]);
  return <div ref={ref} className="h-48" />;
}

function Card({ label, value, color, accent, tip }: {
  label: string; value: string; color?: string; accent?: boolean; tip?: string;
}) {
  return (
    <div
      className="rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 shadow-sm"
      title={tip}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-[10px] font-mono tracking-widest text-muted uppercase">{label}</div>
        {tip && <span className="text-[10px] text-muted font-mono" aria-hidden>ℹ</span>}
      </div>
      <div
        className="mt-1 font-mono text-lg font-bold"
        style={{ color: color ?? (accent ? "#E04F33" : "#F4F3F6") }}
      >
        {value}
      </div>
    </div>
  );
}

function Centered({ children, spinner }: { children: React.ReactNode; spinner?: boolean }) {
  return (
    <div className="flex h-full items-center justify-center bg-basalt">
      <div className="flex items-center gap-3 text-sm text-pewter font-display">
        {spinner && <div className="h-4 w-4 animate-spin rounded-full border-2 border-basalt-400 border-t-terra" />}
        {children}
      </div>
    </div>
  );
}
