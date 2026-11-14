import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import { useStore, type ViewName } from "../store";
import { EMBEREYE_LOGO_DATA_URI } from "../assets/logoData";
import { clearCalendarCache } from "../lib/calendarCache";

const VIEWS: { id: ViewName; label: string }[] = [
  { id: "calendar", label: "Calendar Matrix" },
  { id: "map", label: "Geospatial Map" },
  { id: "analytics", label: "Episodes & Analytics" },
];

export default function TopBar() {
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const aoiId = useStore((s) => s.aoiId);
  const rangeStart = useStore((s) => s.rangeStart);
  const rangeEnd = useStore((s) => s.rangeEnd);
  const health = useStore((s) => s.health);
  const meta = useStore((s) => s.meta);
  const setMeta = useStore((s) => s.setMeta);
  const initRangeFromRecord = useStore((s) => s.initRangeFromRecord);
  const filters = useStore((s) => s.filters);
  const bumpDataRevision = useStore((s) => s.bumpDataRevision);
  const setMethodsOpen = useStore((s) => s.setMethodsOpen);
  const setMapToast = useStore((s) => s.setMapToast);
  const [showInfo, setShowInfo] = useState(false);
  const [liveBusy, setLiveBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const archiveRef = useRef<HTMLDivElement>(null);
  const exportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showInfo && !exportOpen) return;
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (archiveRef.current && !archiveRef.current.contains(target)) setShowInfo(false);
      if (exportRef.current && !exportRef.current.contains(target)) setExportOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowInfo(false);
        setExportOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutside);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [showInfo, exportOpen]);

  const ds = meta?.data_source;
  const readOnly = meta?.public_read_only === true;
  const nPts = ds?.n_hotspots ?? 0;
  const firstYear = ds?.first_acquisition?.slice(0, 4) ?? "2000";
  const lastYear = ds?.last_acquisition?.slice(0, 4) ?? "2026";
  const sourceShort = ds?.kind === "FIRMS_ARCHIVE" ? "REAL FIRMS"
    : ds?.kind === "MIXED" ? "MIXED"
    : ds?.kind === "SYNTHETIC_SEED" ? "SYNTHETIC"
    : ds?.kind === "EMPTY" ? "NO DATA" : "ARCHIVE";
  const archiveLabel = `${sourceShort} · ${firstYear}–${lastYear} · ${nPts > 0 ? nPts.toLocaleString() : "—"} PTS`;

  function exportData(level: "harmonized" | "hotspots", format: "csv" | "geojson") {
    if (!aoiId) return;
    setExportOpen(false);
    window.open(
      api.exportUrl({
        aoi: aoiId, level, format, start: rangeStart, end: rangeEnd,
        ...(level === "hotspots" ? {
          sensor: filters.sensor,
          confidence: filters.confidence,
          daynight: filters.daynight,
          min_frp: filters.minFrp ?? undefined,
          hide_static: filters.hideStaticSources,
        } : {}),
      }),
      "_blank",
      "noopener,noreferrer",
    );
  }

  async function onLiveFetch() {
    if (!aoiId) return;
    setLiveBusy(true);
    try {
      const res = await api.liveFetch(aoiId, 2);
      // New rows invalidate every cached calendar slice for this session.
      clearCalendarCache();
      bumpDataRevision();
      void api.meta().then((nextMeta) => {
        setMeta(nextMeta);
        const first = nextMeta.data_source.first_acquisition;
        const last = nextMeta.data_source.last_acquisition;
        if (first && last) initRangeFromRecord(first, last);
      }).catch(() => undefined);
      setMapToast(`LIVE FETCH ${res.status}: ${res.inserted} rows. ${res.message}`);
    } catch (e) {
      setMapToast(`LIVE FETCH failed: ${(e as Error).message}`);
    } finally {
      setLiveBusy(false);
    }
  }

  return (
    <header className="z-40 flex min-h-14 shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-basalt-300 bg-basalt-100 px-3 py-2 sm:h-14 sm:flex-nowrap sm:gap-5 sm:px-4 sm:py-0">
      {/* Left: Logo + wordmark */}
      <div className="flex items-center gap-3 shrink-0">
        <img
          src={EMBEREYE_LOGO_DATA_URI}
          alt="EmberEye Project Logo"
          className="h-9 w-9 rounded-xl object-contain ring-1 ring-white/10 shadow-lg"
        />
        <div className="leading-tight">
          <div className="flex items-center gap-2">
            <span className="font-display text-sm font-bold tracking-[0.15em] text-alabaster">
              EMBEREYE
            </span>
          </div>
          <div className="hidden font-mono text-[9px] tracking-widest text-muted uppercase sm:block">
            NASA Space Apps 2026 · FIRMS Engine
          </div>
        </div>
      </div>

      {/* Center: view switcher pill */}
      <nav
        className="order-3 mx-auto flex max-w-full items-center gap-[2px] overflow-x-auto rounded-full border border-basalt-300 bg-basalt-200 p-[3px] scrollbar-none sm:order-none"
        aria-label="views"
      >
        {VIEWS.map((v) => (
          <button
            key={v.id}
            onClick={() => setView(v.id)}
            className={`shrink-0 rounded-full px-2.5 py-1.5 text-[10px] font-semibold tracking-wide transition-all font-display sm:px-4 sm:text-[11px] ${
              view === v.id
                ? "bg-terra text-alabaster shadow-sm"
                : "text-pewter hover:text-alabaster hover:bg-basalt-300"
            }`}
            aria-pressed={view === v.id}
          >
            {v.label}
          </button>
        ))}
      </nav>

      {/* Right: telemetry + actions */}
      <div className="flex w-full min-w-0 items-center justify-end gap-1.5 overflow-x-auto scrollbar-none sm:w-auto sm:shrink-0 sm:gap-2 sm:overflow-visible">
        {readOnly && (
          <span role="status" className="shrink-0 rounded-md border border-amber/40 bg-amber/10 px-2 py-1.5 font-mono text-[9px] font-bold tracking-wider text-amber">
            READ-ONLY DEMO
          </span>
        )}
        {aoiId && (
          <button
            onClick={onLiveFetch}
            disabled={readOnly || liveBusy || health?.firms.key_configured === false}
            aria-label={readOnly ? "Live fetch disabled in public read-only mode" : health?.firms.key_configured === false ? "Live fetch unavailable: configure FIRMS_MAP_KEY" : "Fetch the last two days from NASA FIRMS"}
            className={`rounded-lg border px-2.5 py-1.5 font-display text-[10px] font-bold transition-colors tracking-wide disabled:opacity-70 ${readOnly || health?.firms.key_configured === false ? "border-amber/40 bg-amber/10 text-amber" : "border-jade/40 bg-jade/10 text-jade hover:bg-jade/20"}`}
            title={readOnly ? "Disabled in public read-only demo mode" : health?.firms.key_configured === false ? "Configure FIRMS_MAP_KEY to enable live NASA FIRMS fetch" : "Fetch last 2 days from NASA FIRMS"}
          >
            {readOnly ? "Read-only" : liveBusy ? "Fetching…" : health?.firms.key_configured === false
              ? <><span className="sm:hidden">Key needed</span><span className="hidden sm:inline">FIRMS key needed</span></>
              : <><span className="sm:hidden">Live 2d</span><span className="hidden sm:inline">Live Fetch (2d)</span></>}
          </button>
        )}

        {/* Archive telemetry chip */}
        <div className="relative" ref={archiveRef}>
          <button
            onClick={() => setShowInfo((v) => !v)}
            aria-expanded={showInfo}
            aria-haspopup="dialog"
            aria-label="Archive provenance information"
            className="flex items-center gap-1.5 rounded-md border border-basalt-300 bg-basalt-200 px-2.5 py-1.5 font-mono text-[10px] font-medium tracking-wider text-pewter hover:text-alabaster transition-colors"
            title="Archive provenance"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${ds?.kind === "FIRMS_ARCHIVE" ? "bg-jade" : ds?.kind === "MIXED" ? "bg-amber" : "bg-terra"}`} />
            {archiveLabel}
          </button>
          {showInfo && ds && (
            <div role="dialog" aria-label="Archive provenance details" className="absolute right-0 top-9 z-50 w-80 max-w-[90vw] rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 text-[11px] leading-relaxed text-pewter shadow-2xl">
              <div className="mb-2 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-muted">
                <span>{ds.n_hotspots.toLocaleString()} hotspot rows</span>
                {Object.entries(ds.products).map(([k, v]) => (
                  <span key={k}>{k}: {v.toLocaleString()}</span>
                ))}
                {ds.first_acquisition && (
                  <span>acq {ds.first_acquisition} → {ds.last_acquisition ?? "—"}</span>
                )}
                {ds.last_ingest_at && (
                  <span>last ingest {ds.last_ingest_at.slice(0, 16).replace("T", " ")} ({ds.last_ingest_status})</span>
                )}
              </div>
              {ds.detail}
              {meta?.seed_disclaimer && (
                <div className="mt-2 border-t border-basalt-300 pt-2 text-muted">{meta.seed_disclaimer}</div>
              )}
              <div className="mt-2 text-[10px] text-muted font-mono">
                seed v{health?.seed_version ?? "—"} · API v{meta?.version ?? "—"}
              </div>
            </div>
          )}
          {showInfo && !ds && (
            <div className="absolute right-0 top-9 z-50 w-64 rounded-xl border border-basalt-300 bg-basalt-100 p-3 shadow-2xl text-[11px] text-pewter">
              Loading archive information…
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={() => setMethodsOpen(true)}
          className="rounded-md border border-basalt-300 bg-basalt-200 px-3 py-1.5 text-[11px] font-semibold text-pewter hover:text-alabaster hover:border-basalt-400 transition-colors font-display tracking-wide"
        >
          Methodology
        </button>

        {/* Export Dropdown (Dual CSV + GeoJSON) */}
        <div className="relative" ref={exportRef}>
          <button
            onClick={() => setExportOpen((o) => !o)}
            aria-expanded={exportOpen}
            aria-haspopup="menu"
            disabled={!aoiId}
            className="flex items-center gap-1.5 rounded-md bg-terra px-3 py-1.5 text-[11px] font-bold text-alabaster hover:bg-ember-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-display tracking-wide shadow-sm"
            style={{ boxShadow: aoiId ? "0 0 12px rgba(224,79,51,0.25)" : undefined }}
            title="Download data archives"
          >
            <span>Export</span>
            <svg
              className={`h-3 w-3 transition-transform ${exportOpen ? "rotate-180" : ""}`}
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path d="M2 4l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>

          {exportOpen && (
            <>
              <div className="fixed inset-0 z-40" aria-hidden="true" onClick={() => setExportOpen(false)} />
              <div role="menu" aria-label="Export dataset" className="absolute right-0 top-full z-50 mt-1.5 w-64 rounded-xl border border-basalt-300 bg-basalt-100 p-1.5 shadow-2xl">
                <div className="px-2.5 py-1 text-[9px] font-mono font-bold tracking-widest text-muted uppercase">
                  Export Dataset
                </div>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => exportData("harmonized", "csv")}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs text-pewter hover:bg-basalt-200 hover:text-alabaster transition-colors"
                >
                  <div>
                    <div className="font-semibold text-alabaster font-display">Harmonized Calendar Series</div>
                    <div className="text-[10px] text-muted">Daily unified fire counts &amp; anomalies</div>
                  </div>
                  <span className="rounded bg-terra/15 px-1.5 py-0.5 font-mono text-[9px] font-bold text-terra">CSV</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => exportData("hotspots", "csv")}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs text-pewter hover:bg-basalt-200 hover:text-alabaster transition-colors"
                >
                  <div>
                    <div className="font-semibold text-alabaster font-display">Hotspot Detections</div>
                    <div className="text-[10px] text-muted">Filtered satellite points with FRP</div>
                  </div>
                  <span className="rounded bg-amber/15 px-1.5 py-0.5 font-mono text-[9px] font-bold text-amber">CSV</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => exportData("hotspots", "geojson")}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs text-pewter hover:bg-basalt-200 hover:text-alabaster transition-colors"
                >
                  <div>
                    <div className="font-semibold text-alabaster font-display">Geospatial Hotspots</div>
                    <div className="text-[10px] text-muted">Point geometries for GIS tools</div>
                  </div>
                  <span className="rounded bg-jade/15 px-1.5 py-0.5 font-mono text-[9px] font-bold text-jade">JSON</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
