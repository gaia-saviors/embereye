import { useEffect, useState } from "react";

import { api } from "./api/client";
import ErrorBoundary from "./components/ErrorBoundary";
import TopBar from "./components/TopBar";
import ControlRibbon from "./components/ControlRibbon";
import TimelineScrubber from "./components/TimelineScrubber";
import DateRangeBar from "./components/DateRangeBar";
import MethodsModal from "./components/MethodsModal";
import CalendarView from "./views/CalendarView";
import MapView from "./views/MapView";
import AnalyticsView from "./views/AnalyticsView";
import { useStore } from "./store";

export default function App() {
  const view = useStore((s) => s.view);
  const aoiId = useStore((s) => s.aoiId);
  const setMeta = useStore((s) => s.setMeta);
  const setHealth = useStore((s) => s.setHealth);
  const setAois = useStore((s) => s.setAois);
  const setAoiId = useStore((s) => s.setAoiId);
  const initRangeFromRecord = useStore((s) => s.initRangeFromRecord);
  const mapToast = useStore((s) => s.mapToast);
  const setMapToast = useStore((s) => s.setMapToast);
  const [aoiLoadState, setAoiLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [aoiLoadError, setAoiLoadError] = useState<string | null>(null);
  const [aoiReload, setAoiReload] = useState(0);

  useEffect(() => {
    let alive = true;
    // Phase 3A (defect 1): /api/meta is the SINGLE source of the badge.
    // Retry with backoff until it succeeds.
    let attempt = 0;
    const fetchMeta = () => {
      attempt += 1;
      api
        .meta()
        .then((m) => {
          if (!alive) return;
          setMeta(m);
          const ds = m.data_source;
          if (ds.first_acquisition && ds.last_acquisition) {
            // Defect 8: derive the timeline default from the record itself.
            initRangeFromRecord(ds.first_acquisition, ds.last_acquisition);
          }
        })
        .catch(() => {
          if (!alive) return;
          if (attempt <= 6) setTimeout(fetchMeta, 1200 * attempt);
          else setMapToast("Archive metadata is unavailable. Check the API connection and reload to retry.");
        });
    };
    fetchMeta();
    api
      .health()
      .then((h) => alive && setHealth(h))
      .catch((error) => alive && setMapToast(`Service health unavailable: ${(error as Error).message}`));
    const t = setInterval(() => {
      api
        .health()
        .then((h) => alive && setHealth(h))
        .catch((error) => {
          if (alive && useStore.getState().health === null) {
            setMapToast(`Service health unavailable: ${(error as Error).message}`);
          }
        });
    }, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [setMeta, setHealth, setMapToast, initRangeFromRecord]);

  useEffect(() => {
    let alive = true;
    setAoiLoadState("loading");
    setAoiLoadError(null);
    api.listAois()
      .then((aois) => {
        if (!alive) return;
        setAois(aois);
        setAoiLoadState("ready");
        const firstWithData = aois.find((a) => a.has_data) ?? aois[0];
        if (firstWithData && useStore.getState().aoiId === null) setAoiId(firstWithData.id);
      })
      .catch((error) => {
        if (!alive) return;
        setAoiLoadError((error as Error).message);
        setAoiLoadState("error");
      });
    return () => { alive = false; };
  }, [aoiReload, setAois, setAoiId]);

  return (
    <ErrorBoundary>
      <div className="flex h-screen min-h-0 flex-col bg-basalt text-alabaster">
        {/* Tier 1: Primary Navigation Bar */}
        <TopBar />
        {/* Tier 2: Horizontal Mission Control & Filter Ribbon */}
        <ControlRibbon
          aoiLoadState={aoiLoadState}
          aoiLoadError={aoiLoadError}
          retryAois={() => setAoiReload((attempt) => attempt + 1)}
        />
        {/* 100% width workspace */}
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="min-h-0 flex-1 overflow-hidden">
            {view === "calendar" && <CalendarView />}
            {view === "map" && <MapView />}
            {view === "analytics" && <AnalyticsView />}
          </div>
          {/* Bottom bar: timeline scrubber for map, date range for others */}
          {aoiId && (view === "map" ? <TimelineScrubber /> : <DateRangeBar />)}
        </main>
        <MethodsModal />
        {mapToast && (
          <button
            type="button"
            role="status"
            aria-live="polite"
            onClick={() => setMapToast(null)}
            className="fixed bottom-20 left-1/2 z-[60] flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-xl border border-basalt-400 bg-basalt-100/95 px-4 py-2.5 text-left text-xs text-alabaster shadow-2xl backdrop-blur sm:max-w-xl"
            title="Dismiss notification"
          >
            <span className="min-w-0 truncate">{mapToast}</span>
            <span aria-hidden className="shrink-0 text-muted">×</span>
          </button>
        )}
      </div>
    </ErrorBoundary>
  );
}
