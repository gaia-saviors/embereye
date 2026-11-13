import { useState } from "react";

import { api } from "../api/client";
import { useStore, type ViewName } from "../store";
import { EMBEREYE_LOGO_DATA_URI } from "../assets/logoData";

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
  const setMethodsOpen = useStore((s) => s.setMethodsOpen);
  const setMapToast = useStore((s) => s.setMapToast);
  const [showInfo, setShowInfo] = useState(false);
  const [liveBusy, setLiveBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);

  const ds = meta?.data_source;
  const nPts = ds?.n_hotspots ?? 0;
  const firstYear = ds?.first_acquisition?.slice(0, 4) ?? "2000";
  const lastYear = ds?.last_acquisition?.slice(0, 4) ?? "2026";
  const archiveLabel = `ARCHIVE: ${firstYear}–${lastYear} · ${nPts > 0 ? nPts.toLocaleString() : "—"} PTS`;

  function exportData(level: "harmonized" | "hotspots", format: "csv" | "geojson") {
    if (!aoiId) return;
    setExportOpen(false);
    window.open(
      api.exportUrl({ aoi: aoiId, level, format, start: rangeStart, end: rangeEnd }),
      "_blank",
    );
  }

  async function onLiveFetch() {
    if (!aoiId) return;
    setLiveBusy(true);
    try {
      const res = await api.liveFetch(aoiId, 2);
      setMapToast(`LIVE FETCH ${res.status}: ${res.inserted} rows. ${res.message}`);
    } catch (e) {
      setMapToast(`LIVE FETCH failed: ${(e as Error).message}`);
    } finally {
      setLiveBusy(false);
    }
  }

  return (
    <header className="z-40 flex h-14 shrink-0 items-center gap-5 border-b border-basalt-300 bg-basalt-100 px-4">
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
          <div className="font-mono text-[9px] tracking-widest text-muted uppercase">
            NASA Space Apps 2026 · FIRMS Engine
          </div>
        </div>
      </div>

      {/* Center: view switcher pill */}
      <nav
        className="mx-auto flex items-center rounded-full border border-basalt-300 bg-basalt-200 p-[3px] gap-[2px]"
        aria-label="views"
      >
        {VIEWS.map((v) => (
          <button
            key={v.id}
            onClick={() => setView(v.id)}
            className={`rounded-full px-4 py-1.5 text-[11px] font-semibold tracking-wide transition-all font-display ${
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
      <div className="flex items-center gap-2 shrink-0">
        {aoiId && (
          <button
            onClick={onLiveFetch}
            disabled={liveBusy}
            className="rounded-lg border border-jade/40 bg-jade/10 px-2.5 py-1.5 font-display text-[10px] font-bold text-jade hover:bg-jade/20 disabled:opacity-40 transition-colors tracking-wide"
            title="Fetch last 2 days from NASA FIRMS"
          >
            {liveBusy ? "Fetching…" : "Live Fetch (2d)"}
          </button>
        )}

        {/* Archive telemetry chip */}
        <div className="relative">
          <button
            onClick={() => setShowInfo((v) => !v)}
            className="flex items-center gap-1.5 rounded-md border border-basalt-300 bg-basalt-200 px-2.5 py-1.5 font-mono text-[10px] font-medium tracking-wider text-pewter hover:text-alabaster transition-colors"
            title="Archive provenance"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-terra animate-pulse" />
            {archiveLabel}
          </button>
          {showInfo && ds && (
            <div className="absolute right-0 top-9 z-50 w-80 rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 shadow-2xl text-[11px] leading-relaxed text-pewter">
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
          onClick={() => setMethodsOpen(true)}
          className="rounded-md border border-basalt-300 bg-basalt-200 px-3 py-1.5 text-[11px] font-semibold text-pewter hover:text-alabaster hover:border-basalt-400 transition-colors font-display tracking-wide"
        >
          Methodology
        </button>

        {/* Export Dropdown (Dual CSV + GeoJSON) */}
        <div className="relative">
          <button
            onClick={() => setExportOpen((o) => !o)}
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
              <div className="fixed inset-0 z-40" onClick={() => setExportOpen(false)} />
              <div className="absolute right-0 top-full mt-1.5 z-50 w-64 rounded-xl border border-basalt-300 bg-basalt-100 p-1.5 shadow-2xl">
                <div className="px-2.5 py-1 text-[9px] font-mono font-bold tracking-widest text-muted uppercase">
                  Export Dataset
                </div>
                <button
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
