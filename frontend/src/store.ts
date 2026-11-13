import { create } from "zustand";

import type {
  Aoi,
  CalendarResponse,
  HealthResponse,
  MetaResponse,
  Provenance,
} from "./api/types";

export type ViewName = "calendar" | "map" | "analytics";

export interface Filters {
  sensor: "all" | "MODIS" | "VIIRS";
  confidence: "all" | "LOW" | "NOMINAL" | "HIGH";
  daynight: "all" | "D" | "N";
  minFrp: number | null;
  hideStaticSources: boolean;
  anomalyOverlay: boolean;
}

interface EmberState {
  view: ViewName;
  setView: (v: ViewName) => void;

  meta: MetaResponse | null;
  health: HealthResponse | null;
  setMeta: (m: MetaResponse) => void;
  setHealth: (h: HealthResponse) => void;

  aois: Aoi[];
  setAois: (a: Aoi[]) => void;
  aoiId: string | null;
  setAoiId: (id: string) => void;

  rangeStart: string;
  rangeEnd: string;
  setRange: (start: string, end: string) => void;
  // Phase 3A (defect 8): the full record coverage, derived from /api/meta
  // (first/last acquisition). The timeline defaults to the FULL record -
  // not a hardcoded 2012-01-01 - and date inputs clamp to this range.
  recordStart: string | null;
  recordEnd: string | null;
  rangeInitialized: boolean;
  initRangeFromRecord: (start: string, end: string) => void;

  cursorDate: string | null;
  setCursorDate: (d: string | null) => void;

  playing: boolean;
  setPlaying: (p: boolean) => void;
  playSpeedDaysPerTick: number;
  setPlaySpeed: (n: number) => void;

  filters: Filters;
  setFilters: (f: Partial<Filters>) => void;

  calendar: CalendarResponse | null;
  setCalendar: (c: CalendarResponse | null) => void;

  selectedCellDate: string | null;
  setSelectedCellDate: (d: string | null) => void;

  methodsOpen: boolean;
  setMethodsOpen: (b: boolean) => void;

  // Phase 2: include the static + night layers (from 2012) in the headline
  // totals. Default OFF - the comparable series is the headline; the layers
  // are burning activity too (challenge text), so they are always SHOWN and
  // never hidden silently.
  includeLayers: boolean;
  setIncludeLayers: (b: boolean) => void;

  mapToast: string | null;
  setMapToast: (s: string | null) => void;

  drawActive: boolean;
  setDrawActive: (b: boolean) => void;
}

const todayIso = new Date().toISOString().slice(0, 10);

export const useStore = create<EmberState>((set) => ({
  view: "calendar",
  setView: (v) => set({ view: v }),

  meta: null,
  health: null,
  setMeta: (m) => set({ meta: m }),
  setHealth: (h) => set({ health: h }),

  aois: [],
  setAois: (a) => set({ aois: a }),
  aoiId: null,
  // No-op when the same AOI is re-selected: resetting calendar/cursor would
  // wipe loaded data without triggering a refetch (deps unchanged).
  setAoiId: (id) =>
    set((s) =>
      s.aoiId === id
        ? {}
        : { aoiId: id, calendar: null, selectedCellDate: null, cursorDate: null },
    ),

  rangeStart: "2012-01-01",
  rangeEnd: todayIso,
  setRange: (start, end) => set({ rangeStart: start, rangeEnd: end }),
  recordStart: null,
  recordEnd: null,
  rangeInitialized: false,
  initRangeFromRecord: (start, end) =>
    set((s) =>
      s.rangeInitialized
        ? { recordStart: start, recordEnd: end }
        : { recordStart: start, recordEnd: end, rangeInitialized: true, rangeStart: start, rangeEnd: end },
    ),

  cursorDate: null,
  setCursorDate: (d) => set({ cursorDate: d }),

  playing: false,
  setPlaying: (p) => set({ playing: p }),
  playSpeedDaysPerTick: 1,
  setPlaySpeed: (n) => set({ playSpeedDaysPerTick: n }),

  filters: {
    sensor: "all",
    confidence: "all",
    daynight: "all",
    minFrp: null,
    hideStaticSources: true,
    anomalyOverlay: true,
  },
  setFilters: (f) => set((s) => ({ filters: { ...s.filters, ...f } })),

  calendar: null,
  setCalendar: (c) => set({ calendar: c }),

  selectedCellDate: null,
  setSelectedCellDate: (d) => set({ selectedCellDate: d }),

  methodsOpen: false,
  setMethodsOpen: (b) => set({ methodsOpen: b }),

  includeLayers: false,
  setIncludeLayers: (b) => set({ includeLayers: b }),

  mapToast: null,
  setMapToast: (s) => set({ mapToast: s }),

  drawActive: false,
  setDrawActive: (b) => set({ drawActive: b }),
}));

export const PROVENANCE_LABEL: Record<Provenance, string> = {
  MODIS_TRANSFERRED: "MODIS era (transferred)",
  VIIRS_NATIVE: "VIIRS era (native)",
  NO_DATA: "No data",
  // legacy values (pre-Phase-2 databases)
  MODIS_ONLY: "MODIS era (transferred)",
  OVERLAP_FUSED: "Overlap (fused)",
  VIIRS_FORWARD: "VIIRS era (native)",
};

export const PROVENANCE_COLOR: Record<Provenance, string> = {
  MODIS_TRANSFERRED: "#E5A93C",  // Champagne Amber
  VIIRS_NATIVE: "#10B981",       // Emerald Jade
  NO_DATA: "#1C1A22",            // Basalt-200
  MODIS_ONLY: "#E5A93C",         // Champagne Amber
  OVERLAP_FUSED: "#6B6180",      // Warm purple-grey
  VIIRS_FORWARD: "#10B981",      // Emerald Jade
};
