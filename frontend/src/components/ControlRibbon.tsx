import { useEffect, useState } from "react";

import { api } from "../api/client";
import { useStore } from "../store";

const formatLat = (value: number) => `${Math.abs(value).toFixed(1)}°${value < 0 ? "S" : "N"}`;
const formatLon = (value: number) => `${Math.abs(value).toFixed(1)}°${value < 0 ? "W" : "E"}`;

type AoiLoadState = "loading" | "ready" | "error";

/** Tier 2 — Full-width horizontal Mission Control & Filter Ribbon.
 * Replaces the fixed left sidebar. Contains: region selector,
 * + Draw AOI, + Lat/Lon BBox, and all filter controls. */
export default function ControlRibbon({
  aoiLoadState = "loading",
  aoiLoadError = null,
  retryAois,
}: {
  aoiLoadState?: AoiLoadState;
  aoiLoadError?: string | null;
  retryAois?: () => void;
}) {
  const aois = useStore((s) => s.aois);
  const readOnly = useStore((s) => s.meta?.public_read_only === true);
  const aoiId = useStore((s) => s.aoiId);
  const setAoiId = useStore((s) => s.setAoiId);
  const setAois = useStore((s) => s.setAois);
  const filters = useStore((s) => s.filters);
  const setFilters = useStore((s) => s.setFilters);
  const setMapToast = useStore((s) => s.setMapToast);
  const bumpDataRevision = useStore((s) => s.bumpDataRevision);
  const setView = useStore((s) => s.setView);
  const setDrawActive = useStore((s) => s.setDrawActive);

  const [aoiOpen, setAoiOpen] = useState(false);
  const [bboxOpen, setBboxOpen] = useState(false);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [bboxName, setBboxName] = useState("");
  const [bboxNorth, setBboxNorth] = useState("");
  const [bboxSouth, setBboxSouth] = useState("");
  const [bboxWest, setBboxWest] = useState("");
  const [bboxEast, setBboxEast] = useState("");
  const [bboxSubmitting, setBboxSubmitting] = useState(false);

  useEffect(() => {
    if (!aoiOpen && !bboxOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAoiOpen(false);
        setBboxOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [aoiOpen, bboxOpen]);

  useEffect(() => {
    if (!readOnly) return;
    setAoiOpen(false);
    setBboxOpen(false);
    setDrawActive(false);
  }, [readOnly, setDrawActive]);

  const activeAoi = aois.find((a) => a.id === aoiId);

  async function refreshCreatedAoi(id: string, jobId?: string | null) {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      try {
        const current = await api.getAoi(id);
        const activeJobId = jobId ?? current.job_id;
        if (!activeJobId) continue;
        const job = await api.job(activeJobId);
        if (job.status === "DONE" || job.status === "FAILED") {
          setAois(await api.listAois());
          bumpDataRevision();
          setMapToast(job.status === "DONE"
            ? `AOI "${current.name}" is ready.`
            : `AOI "${current.name}" build failed: ${job.message || "check the server job log"}`);
          return;
        }
      } catch {
        // Retry transient visibility/connection failures while the build is running.
      }
    }
    setMapToast("AOI is still processing. Refresh the region list in a moment.");
  }

  async function handleCreateBbox(e: React.FormEvent) {
    e.preventDefault();
    if (readOnly) return;
    const n = parseFloat(bboxNorth);
    const s = parseFloat(bboxSouth);
    const w = parseFloat(bboxWest);
    const es = parseFloat(bboxEast);
    if (
      ![n, s, w, es].every(Number.isFinite) ||
      n > 90 || n < -90 || s > 90 || s < -90 ||
      es > 180 || es < -180 || w > 180 || w < -180 ||
      s >= n || w >= es
    ) {
      setMapToast("Invalid BBox: latitude must be -90..90, longitude -180..180, with South < North and West < East.");
      return;
    }
    setBboxSubmitting(true);
    try {
      const ring = [[w, s], [es, s], [es, n], [w, n], [w, s]];
      const name = bboxName.trim() || `BBox (${s.toFixed(2)},${w.toFixed(2)})`;
      const created = await api.createAoi(name, "Manual Lat/Lon Bounding Box", {
        type: "Polygon",
        coordinates: [ring],
      });
      setAois([created, ...aois]);
      setAoiId(created.id);
      setBboxOpen(false);
      setBboxName(""); setBboxNorth(""); setBboxSouth(""); setBboxWest(""); setBboxEast("");
      setMapToast(`Created AOI: ${created.name}. Building its harmonized series…`);
      void refreshCreatedAoi(created.id, created.job_id);
    } catch (err) {
      setMapToast(`Failed to create BBox AOI: ${(err as Error).message}`);
    } finally {
      setBboxSubmitting(false);
    }
  }

  return (
    <div className="relative z-40 flex shrink-0 flex-wrap items-center gap-2 border-b border-basalt-300 bg-basalt-100 px-3 py-2 sm:gap-2.5 sm:px-4 overflow-visible">

      {/* ── Region selector ── */}
      <div className="relative min-w-0 flex-1 sm:flex-none">
        <button
          onClick={() => { setAoiOpen(!aoiOpen); setBboxOpen(false); }}
          aria-expanded={aoiOpen}
          aria-haspopup="listbox"
          aria-controls="aoi-options"
          className={`flex max-w-full items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors font-display sm:px-3 ${
            aoiOpen
              ? "border-terra bg-terra/10 text-alabaster"
              : "border-basalt-300 bg-basalt-200 text-pewter hover:border-basalt-400 hover:text-alabaster"
          }`}
        >
          <span className="h-2 w-2 rounded-full bg-terra" />
          <span className="max-w-[36vw] truncate sm:max-w-[200px]">
              {activeAoi
              ? `${activeAoi.name} · ${formatLat(activeAoi.south)}–${formatLat(activeAoi.north)}`
              : "Select Region"}
          </span>
          <svg className={`h-3 w-3 transition-transform ${aoiOpen ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M2 4l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        {aoiOpen && (
          <>
            <div
              className="fixed inset-0 z-40"
              onClick={() => setAoiOpen(false)}
              aria-hidden
            />
            <div className="absolute left-0 top-full mt-1.5 z-50 w-80 rounded-xl border border-basalt-300 bg-basalt-100 p-2 shadow-2xl">
              <div className="mb-1.5 px-2 pt-1 text-[9px] font-bold tracking-widest text-muted uppercase font-mono">
                Areas of Interest
              </div>
              <div id="aoi-options" role="listbox" aria-label="Areas of Interest" className="flex max-h-72 flex-col gap-1 overflow-y-auto pr-0.5">
                {aoiLoadState === "loading" && <div role="status" className="px-2.5 py-2 text-xs text-pewter">Loading areas of interest…</div>}
                {aoiLoadState === "error" && (
                  <div role="alert" className="rounded-lg border border-coral/30 bg-coral/10 p-2.5 text-xs text-alabaster">
                    <p>Could not load areas: {aoiLoadError ?? "API request failed."}</p>
                    {retryAois && <button type="button" onClick={(event) => { event.stopPropagation(); retryAois(); }} className="mt-2 rounded-md border border-coral/50 px-2.5 py-1 font-semibold text-coral hover:bg-coral/10">Retry areas</button>}
                  </div>
                )}
                {aoiLoadState === "ready" && aois.length === 0 && <div role="status" className="px-2.5 py-2 text-xs text-pewter">No areas are available in this archive.</div>}
                {aois.map((a) => {
                  const noData = a.has_data === false;
                  return (
                    <div key={a.id} className="flex items-center gap-1 rounded-lg">
                      <button
                        type="button"
                        role="option"
                        aria-selected={aoiId === a.id}
                        onClick={() => {
                          if (!noData) { setAoiId(a.id); setAoiOpen(false); }
                        }}
                        disabled={noData}
                        title={noData ? (a.is_preset ? "No data loaded for this preset" : "AOI is still building or has no data") : undefined}
                        className={`flex min-w-0 flex-1 items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors ${
                          noData
                            ? "cursor-not-allowed text-muted opacity-50"
                            : aoiId === a.id
                              ? "bg-terra/15 text-alabaster font-semibold"
                              : "text-pewter hover:bg-basalt-200 hover:text-alabaster"
                        }`}
                      >
                        <div className="min-w-0">
                          <div className="truncate">{a.name}</div>
                          <div className="truncate font-mono text-[9px] text-muted">
                            {formatLon(a.west)} .. {formatLon(a.east)} · {formatLat(a.south)} .. {formatLat(a.north)}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          {noData ? (
                            <span className="rounded bg-basalt-300 px-1.5 py-0.5 font-mono text-[8px] font-semibold uppercase tracking-wide text-muted">
                              {a.is_preset ? "Unseeded" : "Building"}
                            </span>
                          ) : a.is_preset ? (
                            <span className="rounded bg-jade/15 px-1.5 py-0.5 font-mono text-[8px] font-semibold uppercase tracking-wide text-jade">Loaded</span>
                          ) : null}
                        </div>
                      </button>
                      {!a.is_preset && (
                        <button
                          type="button"
                          disabled={readOnly}
                          onClick={async (e) => {
                            e.stopPropagation();
                            if (!window.confirm(`Delete custom AOI “${a.name}” and its derived data? This cannot be undone.`)) return;
                            try {
                              await api.deleteAoi(a.id);
                              const next = aois.filter((x) => x.id !== a.id);
                              setAois(next);
                              if (aoiId === a.id) setAoiId(next.find((x) => x.has_data)?.id ?? null);
                              setMapToast(`Deleted AOI: ${a.name}`);
                            } catch (err) {
                              setMapToast(`Could not delete AOI: ${(err as Error).message}`);
                            }
                          }}
                          className="shrink-0 rounded-md px-2 py-1.5 font-mono text-[9px] text-muted hover:bg-coral/10 hover:text-coral focus-visible:outline focus-visible:outline-2 focus-visible:outline-coral disabled:cursor-not-allowed disabled:opacity-40"
                          aria-label={`Delete custom AOI ${a.name}`}
                          title={readOnly ? "Disabled in public read-only demo mode" : `Delete ${a.name}`}
                        >
                          DEL
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>

      {/* ── Draw AOI button ── */}
      <button
        type="button"
        disabled={readOnly}
        onClick={() => { setView("map"); setDrawActive(true); setAoiOpen(false); setBboxOpen(false); }}
        className={`${mobileFiltersOpen ? "inline-flex" : "hidden"} shrink-0 rounded-lg border border-dashed border-basalt-400 px-2.5 py-1.5 text-[10px] font-semibold text-muted transition hover:border-terra/60 hover:text-terra font-display tracking-wide disabled:cursor-not-allowed disabled:opacity-40 sm:inline-flex`}
        title={readOnly ? "Disabled in public read-only demo mode" : "Switch to map view and draw a polygon AOI"}
      >
        + Draw AOI
      </button>

      {/* ── Lat/Lon BBox button + popover ── */}
      <div className={`${mobileFiltersOpen ? "block" : "hidden"} relative shrink-0 sm:block`}>
        <button
          type="button"
          disabled={readOnly}
          onClick={() => { setBboxOpen(!bboxOpen); setAoiOpen(false); }}
          aria-expanded={bboxOpen}
          aria-haspopup="dialog"
          aria-controls="bbox-form"
          className={`rounded-lg border border-dashed px-2.5 py-1.5 text-[10px] font-semibold transition font-display tracking-wide disabled:cursor-not-allowed disabled:opacity-40 ${
            bboxOpen
              ? "border-terra/60 text-terra"
              : "border-basalt-400 text-muted hover:border-terra/60 hover:text-terra"
          }`}
          title={readOnly ? "Disabled in public read-only demo mode" : "Enter exact lat/lon bounding box coordinates"}
        >
          + Lat/Lon BBox
        </button>
        {bboxOpen && (
          <>
            <div
              className="fixed inset-0 z-40"
              onClick={() => setBboxOpen(false)}
              aria-hidden
            />
            <div id="bbox-form" role="dialog" aria-label="Create a bounding box area of interest" className="absolute left-0 top-full z-50 mt-1.5 w-72 rounded-xl border border-basalt-300 bg-basalt-100 p-3.5 shadow-2xl">
              <div className="mb-2 font-display text-xs font-bold text-terra tracking-wide">Custom Bounding Box</div>
              <form onSubmit={handleCreateBbox} className="flex flex-col gap-2">
                <input
                  type="text"
                  placeholder="AOI Name (optional)"
                  value={bboxName}
                  onChange={(e) => setBboxName(e.target.value)}
                  className="rounded-lg border border-basalt-300 bg-basalt-200 px-2.5 py-1.5 text-xs text-alabaster placeholder-muted outline-none focus:border-terra transition-colors"
                />
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label htmlFor="bbox-north" className="mb-0.5 block text-[9px] font-semibold uppercase tracking-wide text-muted">North Lat</label>
                    <input id="bbox-north" type="number" min={-90} max={90} step="any" placeholder="31.7" required value={bboxNorth} onChange={(e) => setBboxNorth(e.target.value)}
                      className="w-full rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1 font-mono text-xs text-alabaster outline-none focus:border-terra" />
                  </div>
                  <div>
                    <label htmlFor="bbox-south" className="mb-0.5 block text-[9px] font-semibold uppercase tracking-wide text-muted">South Lat</label>
                    <input id="bbox-south" type="number" min={-90} max={90} step="any" placeholder="30.2" required value={bboxSouth} onChange={(e) => setBboxSouth(e.target.value)}
                      className="w-full rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1 font-mono text-xs text-alabaster outline-none focus:border-terra" />
                  </div>
                  <div>
                    <label htmlFor="bbox-west" className="mb-0.5 block text-[9px] font-semibold uppercase tracking-wide text-muted">West Lon</label>
                    <input id="bbox-west" type="number" min={-180} max={180} step="any" placeholder="29.8" required value={bboxWest} onChange={(e) => setBboxWest(e.target.value)}
                      className="w-full rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1 font-mono text-xs text-alabaster outline-none focus:border-terra" />
                  </div>
                  <div>
                    <label htmlFor="bbox-east" className="mb-0.5 block text-[9px] font-semibold uppercase tracking-wide text-muted">East Lon</label>
                    <input id="bbox-east" type="number" min={-180} max={180} step="any" placeholder="32.1" required value={bboxEast} onChange={(e) => setBboxEast(e.target.value)}
                      className="w-full rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1 font-mono text-xs text-alabaster outline-none focus:border-terra" />
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={readOnly || bboxSubmitting}
                  className="mt-1 rounded-lg bg-terra py-1.5 font-bold text-xs text-alabaster transition hover:bg-ember-400 disabled:opacity-50 shadow-sm"
                >
                  {bboxSubmitting ? "Creating…" : "Create BBox AOI"}
                </button>
              </form>
            </div>
          </>
        )}
      </div>

      {/* ── Divider ── */}
      <div className="h-5 w-px bg-basalt-300 shrink-0" aria-hidden />

      {/* ── Sensor filter ── */}
      <div className="flex shrink-0 items-center gap-1.5">
        <span className="hidden text-[10px] font-semibold uppercase tracking-wide text-muted sm:inline">Sensor</span>
        <div className="seg-pill">
          {(["all", "MODIS", "VIIRS"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setFilters({ sensor: v })}
              aria-pressed={filters.sensor === v}
              className={filters.sensor === v ? "active" : ""}
            >
              {v === "all" ? "All" : v}
            </button>
          ))}
        </div>
      </div>

      <button
        type="button"
        onClick={() => setMobileFiltersOpen((open) => !open)}
        aria-expanded={mobileFiltersOpen}
        aria-label={mobileFiltersOpen ? "Hide secondary filters and AOI tools" : "Show secondary filters and AOI tools"}
        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-basalt-300 bg-basalt-200 px-2.5 py-1.5 text-[10px] font-semibold text-pewter hover:border-basalt-400 hover:text-alabaster sm:hidden"
      >
        Filters <span aria-hidden>{mobileFiltersOpen ? "−" : "+"}</span>
      </button>

      {/* ── Day/Night filter ── */}
      <div className={`${mobileFiltersOpen ? "flex" : "hidden"} shrink-0 items-center gap-1.5 sm:flex`}>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">Time</span>
        <div className="seg-pill">
          {(["all", "D", "N"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setFilters({ daynight: v })}
              aria-pressed={filters.daynight === v}
              className={filters.daynight === v ? "active" : ""}
            >
              {v === "all" ? "All" : v === "D" ? "Day" : "Night"}
            </button>
          ))}
        </div>
      </div>

      {/* ── Confidence filter ── */}
      <div className={`${mobileFiltersOpen ? "flex" : "hidden"} shrink-0 items-center gap-1.5 sm:flex`}>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">Conf.</span>
        <div className="seg-pill">
        {(["all", "LOW", "NOMINAL", "HIGH"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setFilters({ confidence: v })}
              aria-pressed={filters.confidence === v}
              className={filters.confidence === v ? "active" : ""}
            >
              {v === "all" ? "All" : v === "NOMINAL" ? "Nominal" : v === "LOW" ? "Low" : "High"}
            </button>
          ))}
        </div>
      </div>

      {/* ── Min FRP ── */}
      <div className={`${mobileFiltersOpen ? "flex" : "hidden"} shrink-0 items-center gap-1 sm:flex`}>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">Min FRP</span>
        <div className="flex items-center gap-0.5 rounded-lg border border-basalt-300 bg-basalt-200 px-2 py-1">
          <input
            type="number"
            min={0}
            value={filters.minFrp ?? ""}
            onChange={(e) => setFilters({ minFrp: e.target.value === "" ? null : Number(e.target.value) })}
            className="w-14 bg-transparent font-mono text-[11px] text-alabaster outline-none placeholder-muted"
            placeholder="0"
            aria-label="Minimum FRP in megawatts"
          />
          <span className="font-mono text-[10px] text-muted">MW</span>
        </div>
      </div>

      {/* ── Divider ── */}
      <div className={`${mobileFiltersOpen ? "block" : "hidden"} h-5 w-px shrink-0 bg-basalt-300 sm:block`} aria-hidden />

      {/* ── Toggle pills (sleek button pills, no native browser checkboxes) ── */}
      <button
        type="button"
        onClick={() => setFilters({ hideStaticSources: !filters.hideStaticSources })}
        aria-pressed={filters.hideStaticSources}
        title="Exclude persistent type-2 static-source detections from Map and Calendar"
        className={`${mobileFiltersOpen ? "flex" : "hidden"} items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold transition shrink-0 sm:flex ${
          filters.hideStaticSources
            ? "bg-terra/15 border-terra text-alabaster shadow-sm"
            : "bg-basalt-200 border-basalt-300 text-muted hover:border-basalt-400 hover:text-pewter"
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${filters.hideStaticSources ? "bg-terra" : "bg-muted"}`} />
        <span>Exclude Static Flares</span>
      </button>

      <button
        type="button"
        onClick={() => setFilters({ anomalyOverlay: !filters.anomalyOverlay })}
        aria-pressed={filters.anomalyOverlay}
        title="Show or hide p95 and unusually quiet anomaly markers in Calendar"
        className={`${mobileFiltersOpen ? "flex" : "hidden"} items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold transition shrink-0 sm:flex ${
          filters.anomalyOverlay
            ? "bg-terra/15 border-terra text-alabaster shadow-sm"
            : "bg-basalt-200 border-basalt-300 text-muted hover:border-basalt-400 hover:text-pewter"
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${filters.anomalyOverlay ? "bg-terra" : "bg-muted"}`} />
        <span>p95 Anomalies</span>
      </button>
    </div>
  );
}
