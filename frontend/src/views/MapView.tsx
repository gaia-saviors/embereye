import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type Map as MlMap, type StyleSpecification } from "maplibre-gl";

import { api } from "../api/client";
import type {
  GridResponse,
  HotspotFeature,
  SnapshotRedirect,
} from "../api/types";
import { useStore } from "../store";
import { SENSOR_COLOR, UI } from "../lib/colors";
import { addDays, daysBetween, prettyDate } from "../lib/format";
import {
  countInWindow,
  featuresForDay,
  type SnapshotData,
} from "../lib/snapshot";

type BasemapId = "dark" | "sat" | "osm";

const BASEMAP_SOURCES: Record<BasemapId, {
  tiles: string[]; attribution: string; maxzoom: number;
}> = {
  dark: {
    tiles: ["https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, HERE, Garmin, USGS, NGA",
    maxzoom: 16,
  },
  sat: {
    tiles: ["https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, Maxar, Earthstar Geographics",
    maxzoom: 19,
  },
  osm: {
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    attribution: "© OpenStreetMap contributors",
    maxzoom: 19,
  },
};
const BASEMAP_ORDER: BasemapId[] = ["dark", "sat", "osm"];
const BASEMAP_KEY = "embereye:basemap";

function loadBasemap(): BasemapId {
  const saved = (typeof localStorage !== "undefined"
    ? localStorage.getItem(BASEMAP_KEY) : null) as BasemapId | null;
  return saved && BASEMAP_ORDER.includes(saved) ? saved : "dark";
}

function buildBasemapStyle(initial: BasemapId): StyleSpecification {
  const sources: NonNullable<StyleSpecification["sources"]> = {};
  const layers: NonNullable<StyleSpecification["layers"]> = [
    { id: "bg", type: "background", paint: { "background-color": UI.bg } },
  ];
  for (const id of BASEMAP_ORDER) {
    const src = BASEMAP_SOURCES[id];
    sources[id] = {
      type: "raster", tiles: src.tiles, tileSize: 256,
      maxzoom: src.maxzoom, attribution: src.attribution,
    };
    layers.push({
      id: `basemap-${id}`, type: "raster", source: id,
      layout: { visibility: id === initial ? "visible" : "none" },
      paint: id === "dark" ? { "raster-opacity": 0.85 } : {},
    });
  }
  return { version: 8, sources, layers };
}

const EMPTY_FC: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

const snapCache = new Map<string, SnapshotData>();
const snapKeyOf = (
  aoi: string | null, f: { sensor: string; confidence: string; daynight: string; minFrp: number | null },
) => `${aoi}|${f.sensor}|${f.confidence}|${f.daynight}|${f.minFrp ?? ""}`;

function isSnapshotRedirect(
  r: SnapshotData | SnapshotRedirect,
): r is SnapshotRedirect {
  return "mode" in r && (r as SnapshotRedirect).mode === "grid";
}

export default function MapView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [mapReady, setMapReady] = useState(false);

  const aois = useStore((s) => s.aois);
  const aoiId = useStore((s) => s.aoiId);
  const cursorDate = useStore((s) => s.cursorDate);
  const rangeStart = useStore((s) => s.rangeStart);
  const rangeEnd = useStore((s) => s.rangeEnd);
  const filters = useStore((s) => s.filters);
  const mapToast = useStore((s) => s.mapToast);
  const setMapToast = useStore((s) => s.setMapToast);
  const playing = useStore((s) => s.playing);
  const drawActive = useStore((s) => s.drawActive);
  const setDrawActive = useStore((s) => s.setDrawActive);

  const [snap, setSnap] = useState<SnapshotData | null>(null);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [gridRes, setGridRes] = useState<"week" | "month">("week");
  const [rawFeat, setRawFeat] = useState<HotspotFeature[]>([]);
  const [rawTotal, setRawTotal] = useState(0);
  const [rawTruncated, setRawTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [archiveErr, setArchiveErr] = useState<string | null>(null);
  const [showGibs, setShowGibs] = useState(false);
  const [showHeat, setShowHeat] = useState(false);
  const [basemap, setBasemap] = useState<BasemapId>(loadBasemap);

  const drawModeRef = useRef<"off" | "polygon">("off");
  const [, forceRender] = useState(0);
  const drawPts = useRef<number[][]>([]);

  const setDrawMode = useCallback((mode: "off" | "polygon") => {
    drawModeRef.current = mode;
    if (mode === "off") drawPts.current = [];
    forceRender((n) => n + 1);
  }, []);

  useEffect(() => {
    if (drawActive) {
      setDrawMode("polygon");
      setMapToast("Drawing AOI: Click vertices on the map, press Enter to finish or Esc to cancel.");
      setDrawActive(false);
    }
  }, [drawActive, setDrawMode, setMapToast, setDrawActive]);

  // ---- map init -----------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildBasemapStyle(loadBasemap()),
      center: [31, 30.8],
      zoom: 6,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-left");

    map.on("load", () => {
      // AOI Boundary (Warm Gold #E5A93C dashed)
      map.addSource("aois", { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: "aoi-fill", type: "fill", source: "aois",
        paint: { "fill-color": "#E5A93C", "fill-opacity": 0.04 },
      });
      map.addLayer({
        id: "aoi-line", type: "line", source: "aois",
        paint: {
          "line-color": "#E5A93C",
          "line-width": 2,
          "line-opacity": 0.9,
          "line-dasharray": [4, 3],
        },
      });

      // Hotspots source
      map.addSource("hotspots", { type: "geojson", data: EMPTY_FC });

      // Heatmap layer (Magma ramp)
      map.addLayer({
        id: "hs-heat", type: "heatmap", source: "hotspots", layout: { visibility: "none" },
        paint: {
          "heatmap-weight": ["interpolate", ["linear"], ["coalesce", ["get", "frp_mw"], 5], 0, 0.1, 200, 1],
          "heatmap-intensity": 1,
          "heatmap-radius": 26,
          "heatmap-opacity": 0.8,
          "heatmap-color": [
            "interpolate", ["linear"], ["heatmap-density"],
            0, "rgba(12,11,14,0)",
            0.2, "#3B182E",
            0.4, "#7A1F34",
            0.65, "#C83E2B",
            0.85, "#F08C2E",
            1, "#FFF3B0",
          ],
        },
      });

      // Hotspots circles (MODIS: #E5A93C, VIIRS: #E04F33)
      map.addLayer({
        id: "hs-circle", type: "circle", source: "hotspots",
        paint: {
          "circle-color": ["match", ["get", "sensor"], "MODIS", SENSOR_COLOR.MODIS, SENSOR_COLOR.VIIRS],
          "circle-radius": [
            "interpolate", ["linear"], ["coalesce", ["get", "frp_mw"], 1],
            0, 2.5, 10, 3.8, 50, 5.5, 200, 8, 1000, 12,
          ],
          "circle-stroke-width": 0.8,
          "circle-stroke-color": "rgba(12,11,14,0.95)",
          "circle-opacity": 0.94,
        },
      });

      // Aggregated grid cells
      map.addSource("grid-cells", { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: "grid-fill", type: "fill", source: "grid-cells",
        paint: {
          "fill-color": "#E04F33", "fill-opacity": [
            "interpolate", ["linear"], ["coalesce", ["get", "n"], 0],
            1, 0.05, 10, 0.2, 100, 0.45, 1000, 0.7,
          ],
        },
        layout: { visibility: "none" },
      });
      map.addLayer({
        id: "grid-line", type: "line", source: "grid-cells",
        paint: { "line-color": "#E04F33", "line-width": 0.6, "line-opacity": 0.4 },
        layout: { visibility: "none" },
      });

      // Draw line
      map.addSource("draw", { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: "draw-line", type: "line", source: "draw",
        paint: { "line-color": "#E04F33", "line-width": 2, "line-dasharray": [3, 2] },
      });

      map.on("click", "hs-circle", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as Record<string, unknown>;
        new maplibregl.Popup({ closeButton: false })
          .setLngLat((f.geometry as unknown as { coordinates: [number, number] }).coordinates)
          .setHTML(
            `<b style="color:${p.sensor === "MODIS" ? SENSOR_COLOR.MODIS : SENSOR_COLOR.VIIRS}">${p.sensor} · ${p.satellite}</b><br>` +
              `<span style="font-family:JetBrains Mono,monospace;font-size:11px;">${String(p.acq_dt_utc).slice(0, 16).replace("T", " ")} UTC</span><br>` +
              `FRP: <b>${p.frp_mw == null ? "—" : Number(p.frp_mw).toFixed(1)} MW</b> · ` +
              `T4: <b>${p.t4_k == null ? "—" : Number(p.t4_k).toFixed(0)} K</b><br>` +
              `Conf: ${p.confidence} · ${p.daynight === "D" ? "Day" : "Night"}` +
              (p.saturated ? " · <b style=\"color:#F43F5E;\">I4 SATURATED</b>" : "") +
              (p.band21_fallback ? " · band-21 fallback" : "") +
              (p.static_source ? " · static flare (type 2)" : "") +
              (p.firms_product === "NRT"
                ? " · <span style=\"color:#E5A93C\">NRT provisional</span>"
                : p.firms_product === "SP" ? " · SP archive" : ""),
          )
          .addTo(map);
      });
      map.on("mouseenter", "hs-circle", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "hs-circle", () => (map.getCanvas().style.cursor = ""));
      setMapReady(true);
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ---- AOI polygons on map -------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const src = map.getSource("aois") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    src.setData({
      type: "FeatureCollection",
      features: aois.map((a) => ({
        type: "Feature" as const,
        geometry: { type: "Polygon" as const, coordinates: a.geojson.coordinates },
        properties: { id: a.id, name: a.name, selected: a.id === aoiId },
      })),
    });
    const sel = aois.find((a) => a.id === aoiId);
    if (sel) {
      map.fitBounds(
        [
          [sel.west, sel.south],
          [sel.east, sel.north],
        ],
        { padding: 60, duration: 600, maxZoom: 9 },
      );
    }
  }, [aois, aoiId, mapReady]);

  // ---- archive load --------------------------------------------------------
  const attrKey = snapKeyOf(aoiId, filters);
  const [snapRedirect, setSnapRedirect] = useState<SnapshotRedirect | null>(null);

  useEffect(() => {
    if (!aoiId || !mapReady) return;
    let alive = true;
    setArchiveErr(null);
    const cached = snapCache.get(attrKey);
    if (cached) {
      setSnap(cached);
      setSnapRedirect(null);
      setGrid(null);
      return;
    }
    setLoading(true);
    setSnap(null);
    setSnapRedirect(null);
    setGrid(null);
    api
      .snapshot({
        aoi: aoiId,
        sensor: filters.sensor,
        confidence: filters.confidence,
        daynight: filters.daynight,
        min_frp: filters.minFrp ?? undefined,
      })
      .then((res) => {
        if (!alive) return;
        if (isSnapshotRedirect(res)) {
          setMapToast(
            `AOI exceeds point archive limit (${res.n_rows.toLocaleString()} rows) — using aggregated grid mode.`,
          );
          setSnapRedirect(res);
          return;
        }
        snapCache.set(attrKey, res);
        setSnap(res);
      })
      .catch((e) => alive && setArchiveErr((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [aoiId, mapReady, attrKey, setMapToast]);

  useEffect(() => {
    if (!snap) return;
    setGrid(null);
    const map = mapRef.current;
    if (map && map.getSource("grid-cells")) {
      map.setLayoutProperty("grid-fill", "visibility", "none");
      map.setLayoutProperty("grid-line", "visibility", "none");
    }
  }, [snap]);

  // ---- grid overview --------------------------------------------------------
  useEffect(() => {
    if (!aoiId || !mapReady || snap || !snapRedirect) return;
    let alive = true;
    setLoading(true);
    api
      .grid({
        aoi: aoiId, res: gridRes, cell_deg: 0.5,
        sensor: filters.sensor, confidence: filters.confidence,
        daynight: filters.daynight, min_frp: filters.minFrp ?? undefined,
      })
      .then((g) => {
        if (!alive) return;
        setGrid(g);
        const map = mapRef.current;
        const src = map?.getSource("grid-cells") as maplibregl.GeoJSONSource | undefined;
        if (map && src) {
          src.setData({
            type: "FeatureCollection",
            features: g.cells.map((c) => ({
              type: "Feature" as const,
              geometry: {
                type: "Polygon" as const,
                coordinates: [[[c.w, c.s], [c.e, c.s], [c.e, c.n_], [c.w, c.n_], [c.w, c.s]]],
              },
              properties: { n: c.n, frp: c.frp_mw },
            })),
          });
          const vis = snap ? "none" : "visible";
          map.setLayoutProperty("grid-fill", "visibility", vis);
          map.setLayoutProperty("grid-line", "visibility", vis);
        }
      })
      .catch((e) => alive && setArchiveErr((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [aoiId, mapReady, snap, snapRedirect, gridRes, filters]);

  // Viewport tracking for grid mode
  const [viewTick, setViewTick] = useState(0);
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const bump = () => setViewTick((t) => t + 1);
    map.on("moveend", bump);
    return () => { map.off("moveend", bump); };
  }, [mapReady]);

  const [wStart, setWStart] = useState<string>(rangeStart);
  const [wEnd, setWEnd] = useState<string>(rangeEnd);
  useEffect(() => {
    const anchor = cursorDate ?? rangeEnd;
    let ws = addDays(anchor, -60);
    let we = addDays(anchor, 60);
    if (daysBetween(ws, rangeStart) > 0) {
      ws = rangeStart;
      we = addDays(rangeStart, 120);
    }
    if (daysBetween(rangeEnd, we) > 0) {
      we = rangeEnd;
      ws = addDays(rangeEnd, -120);
    }
    setWStart(ws);
    setWEnd(we);
  }, [cursorDate, rangeStart, rangeEnd]);

  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const src = map.getSource("hotspots") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    const day = cursorDate ?? rangeEnd;
    if (snap) {
      src.setData({
        type: "FeatureCollection",
        features: featuresForDay(snap, day, filters.hideStaticSources),
      });
      return;
    }
    if (!grid) {
      src.setData(EMPTY_FC);
      return;
    }
  }, [snap, grid, cursorDate, rangeEnd, mapReady, filters.hideStaticSources]);

  useEffect(() => {
    if (snap || !grid || !aoiId || !mapReady) return;
    const map = mapRef.current;
    if (!map) return;
    const ac = new AbortController();
    const t = window.setTimeout(() => {
      const b = map.getBounds();
      const day = cursorDate ?? rangeEnd;
      api
        .hotspots({
          aoi: aoiId,
          bbox: `${b.getWest().toFixed(4)},${b.getSouth().toFixed(4)},${b.getEast().toFixed(4)},${b.getNorth().toFixed(4)}`,
          start: day, end: day,
          sensor: filters.sensor, confidence: filters.confidence,
          daynight: filters.daynight, min_frp: filters.minFrp ?? undefined,
          limit: 20000,
        }, ac.signal)
        .then((res) => {
          let feats = res.features;
          if (filters.hideStaticSources) {
            feats = feats.filter((f) => !f.properties.static_source);
          }
          setRawFeat(feats);
          setRawTotal(res.meta.total);
          setRawTruncated(res.meta.total > res.meta.returned);
          const src = map.getSource("hotspots") as maplibregl.GeoJSONSource | undefined;
          src?.setData({ type: "FeatureCollection", features: feats });
        })
        .catch((e) => {
          if ((e as Error).name !== "AbortError") {
            setMapToast(`Hotspots unavailable: ${(e as Error).message}`);
          }
        });
    }, 200);
    return () => {
      window.clearTimeout(t);
      ac.abort();
    };
  }, [snap, grid, aoiId, mapReady, cursorDate, rangeEnd, filters, viewTick, setMapToast]);

  // GIBS overlay
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const layerId = "gibs-anom";
    if (showGibs) {
      const day = cursorDate ?? rangeEnd;
      if (map.getSource("gibs")) {
        map.removeLayer(layerId);
        map.removeSource("gibs");
      }
      map.addSource("gibs", {
        type: "raster",
        tiles: [
          `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_Thermal_Anomalies_All/default/${day}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png`,
        ],
        tileSize: 256,
        maxzoom: 9,
        attribution: "NASA GIBS",
      });
      map.addLayer({ id: layerId, type: "raster", source: "gibs", paint: { "raster-opacity": 0.85 } });
    } else if (map.getLayer(layerId)) {
      map.removeLayer(layerId);
      map.removeSource("gibs");
    }
  }, [showGibs, cursorDate, rangeEnd, mapReady]);

  // Heatmap toggle
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    if (map.getLayer("hs-heat")) {
      map.setLayoutProperty("hs-heat", "visibility", showHeat ? "visible" : "none");
      map.setLayoutProperty("hs-circle", "visibility", showHeat ? "none" : "visible");
    }
  }, [showHeat, mapReady]);

  // Basemap switching
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    for (const id of BASEMAP_ORDER) {
      if (map.getLayer(`basemap-${id}`)) {
        map.setLayoutProperty(
          `basemap-${id}`, "visibility", id === basemap ? "visible" : "none");
      }
    }
    try { localStorage.setItem(BASEMAP_KEY, basemap); } catch { /* ignore */ }
  }, [basemap, mapReady]);

  // AOI Creation
  const createAoiFromRing = useCallback(
    async (ring: number[][]) => {
      try {
        const created = await api.createAoi(
          `AOI ${new Date().toISOString().slice(5, 16).replace("T", " ")}`,
          "User-drawn AOI",
          { type: "Polygon", coordinates: [ring] },
        );
        const list = await api.listAois();
        useStore.getState().setAois(list);
        useStore.getState().setAoiId(created.id);
        setMapToast(`AOI "${created.id}" created — harmonization started.`);
        void pollUntilReady(created.id);
      } catch (e) {
        setMapToast(`AOI creation failed: ${(e as Error).message}`);
      }
    },
    [setMapToast],
  );

  async function pollUntilReady(id: string) {
    for (let i = 0; i < 240; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      if (useStore.getState().aoiId !== id) return;
      try {
        await api.calendar(id);
        useStore.getState().setMapToast(`AOI ${id} ready — harmonized series built.`);
        return;
      } catch {
        continue;
      }
    }
  }

  // Shift-drag bbox drawing
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    let start: [number, number] | null = null;

    const onDown = (e: maplibregl.MapMouseEvent) => {
      if (!e.originalEvent.shiftKey) return;
      e.preventDefault();
      start = [e.lngLat.lng, e.lngLat.lat];
      map.dragPan.disable();

      const onMove = (ev: maplibregl.MapMouseEvent) => {
        if (!start) return;
        setDrawBoxSource(map, start, [ev.lngLat.lng, ev.lngLat.lat]);
      };
      const onUp = (ev: maplibregl.MapMouseEvent) => {
        map.off("mousemove", onMove);
        map.off("mouseup", onUp);
        map.dragPan.enable();
        if (!start) return;
        const a = start;
        start = null;
        (map.getSource("draw") as maplibregl.GeoJSONSource).setData(EMPTY_FC);
        const w = Math.min(a[0], ev.lngLat.lng);
        const es = Math.max(a[0], ev.lngLat.lng);
        const s = Math.min(a[1], ev.lngLat.lat);
        const n = Math.max(a[1], ev.lngLat.lat);
        if (Math.abs(es - w) < 0.05 || Math.abs(n - s) < 0.05) return;
        void createAoiFromRing([[w, s], [es, s], [es, n], [w, n], [w, s]]);
      };
      map.on("mousemove", onMove);
      map.on("mouseup", onUp);
    };
    map.on("mousedown", onDown);
    return () => {
      map.off("mousedown", onDown);
    };
  }, [mapReady, createAoiFromRing]);

  // Polygon drawing via click points
  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDrawMode("off");
        (mapRef.current?.getSource("draw") as maplibregl.GeoJSONSource | undefined)?.setData(EMPTY_FC);
      }
      if (e.key === "Enter" && drawModeRef.current === "polygon" && drawPts.current.length >= 3) {
        const ring = [...drawPts.current, drawPts.current[0]];
        setDrawMode("off");
        (mapRef.current?.getSource("draw") as maplibregl.GeoJSONSource | undefined)?.setData(EMPTY_FC);
        await createAoiFromRing(ring);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [createAoiFromRing, setDrawMode]);

  const onMapClick = useCallback(
    (e: maplibregl.MapMouseEvent) => {
      if (drawModeRef.current !== "polygon") return;
      drawPts.current.push([Number(e.lngLat.lng.toFixed(4)), Number(e.lngLat.lat.toFixed(4))]);
      const pts = drawPts.current;
      if (pts.length >= 2) {
        (mapRef.current?.getSource("draw") as maplibregl.GeoJSONSource | undefined)?.setData({
          type: "FeatureCollection",
          features: [{
            type: "Feature",
            geometry: { type: "Polygon", coordinates: [[...pts, pts[0]]] },
            properties: {},
          }],
        });
      }
    },
    [],
  );

  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    map.on("click", onMapClick);
    return () => { map.off("click", onMapClick); };
  }, [mapReady, onMapClick]);

  const day = cursorDate ?? rangeEnd;

  const windowTotal = useMemo(
    () => (snap ? countInWindow(snap, wStart, wEnd) : grid ? rawTotal : 0),
    [snap, grid, rawTotal, wStart, wEnd],
  );
  const dayCount = useMemo(() => {
    if (snap) {
      return featuresForDay(snap, day, filters.hideStaticSources).length;
    }
    return rawFeat.length;
  }, [snap, rawFeat, day, filters.hideStaticSources]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-basalt">
      {/* MapLibre Canvas */}
      <div ref={containerRef} className="h-full w-full" />

      {/* ── Top-Left Floating GIS HUD ── */}
      <div className="glass-hud absolute left-4 top-4 z-10 flex flex-col gap-2 rounded-2xl p-3 text-xs text-pewter shadow-2xl min-w-[200px]">
        {/* Draw status banner (only visible when drawing polygon) */}
        {drawModeRef.current === "polygon" && (
          <div className="rounded-lg bg-terra/20 border border-terra px-2.5 py-1.5 font-mono text-[10px] font-bold text-alabaster tracking-wider animate-pulse flex items-center justify-between">
            <span>DRAWING AOI</span>
            <span className="text-muted font-normal">ENTER/ESC</span>
          </div>
        )}

        {/* Layer Toggle Pills */}
        <div className="flex flex-col gap-1.5">
          <button
            type="button"
            onClick={() => setShowHeat(!showHeat)}
            className={`flex items-center justify-between gap-2.5 rounded-lg px-2.5 py-1.5 text-[10px] font-medium transition border ${
              showHeat
                ? "bg-terra/15 border-terra text-alabaster shadow-sm"
                : "bg-basalt-200 border-basalt-300 text-muted hover:border-basalt-400 hover:text-pewter"
            }`}
          >
            <span>Density Heatmap</span>
            <span className={`h-2 w-2 rounded-full ${showHeat ? "bg-terra" : "bg-muted"}`} />
          </button>

          <button
            type="button"
            onClick={() => setShowGibs(!showGibs)}
            className={`flex items-center justify-between gap-2.5 rounded-lg px-2.5 py-1.5 text-[10px] font-medium transition border ${
              showGibs
                ? "bg-amber/15 border-amber text-alabaster shadow-sm"
                : "bg-basalt-200 border-basalt-300 text-muted hover:border-basalt-400 hover:text-pewter"
            }`}
          >
            <span>NASA GIBS Thermal Layer</span>
            <span className={`h-2 w-2 rounded-full ${showGibs ? "bg-amber" : "bg-muted"}`} />
          </button>
        </div>

        {/* Grid resolution switch (if active) */}
        {!snap && (
          <div className="flex items-center gap-2 border-t border-white/5 pt-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted font-mono">GRID</span>
            {(["week", "month"] as const).map((r) => (
              <button
                key={r}
                onClick={() => setGridRes(r)}
                className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
                  gridRes === r ? "bg-terra text-alabaster" : "bg-basalt-200 text-pewter hover:text-alabaster"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        )}

        {/* Basemap Switcher */}
        <div className="flex items-center gap-1 border-t border-white/5 pt-2">
          {BASEMAP_ORDER.map((id) => (
            <button
              key={id}
              onClick={() => setBasemap(id)}
              aria-pressed={basemap === id}
              className={`rounded-lg px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider transition ${
                basemap === id
                  ? "bg-terra text-alabaster shadow-sm"
                  : "bg-basalt-200 text-pewter hover:text-alabaster"
              }`}
            >
              {id === "dark" ? "Dark" : id === "sat" ? "Satellite" : "OSM"}
            </button>
          ))}
        </div>

        {/* Sensor Legend */}
        <div className="flex items-center justify-between border-t border-white/5 pt-2 text-[10px] font-mono">
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: SENSOR_COLOR.MODIS }} />
            <span className="text-alabaster">MODIS 1km</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: SENSOR_COLOR.VIIRS }} />
            <span className="text-alabaster">VIIRS 375m</span>
          </div>
        </div>
      </div>

      {/* ── Top-Right Telemetry HUD ── */}
      <div className="glass-hud absolute right-4 top-4 z-10 rounded-2xl p-4 text-right shadow-2xl min-w-[220px]">
        <div className="flex items-center justify-between gap-3 mb-1">
          <span className="text-[9px] font-bold tracking-widest text-muted uppercase font-mono">Telemetry Date</span>
          {playing && (
            <span className="flex items-center gap-1 text-[9px] font-bold tracking-wider text-jade uppercase font-mono animate-pulse">
              <span className="h-1.5 w-1.5 rounded-full bg-jade" />
              PLAYING
            </span>
          )}
        </div>

        {/* Large date display */}
        <div className="font-mono text-base font-bold text-alabaster tracking-tight">
          {prettyDate(day)}
        </div>

        {/* Detections count stats */}
        <div className="mt-2.5 pt-2.5 border-t border-white/10 flex flex-col gap-1 text-xs">
          <div className="flex items-center justify-between gap-4">
            <span className="text-[10px] text-muted">Day Detections:</span>
            <span className="font-mono font-bold text-terra">
              {dayCount.toLocaleString()}
              {rawTruncated && <span className="text-[9px] text-amber font-normal"> (capped)</span>}
            </span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <span className="text-[10px] text-muted">Window Total:</span>
            <span className="font-mono font-bold text-alabaster">{windowTotal.toLocaleString()}</span>
          </div>
        </div>

        {loading && (
          <div className="mt-2 text-[10px] text-pewter font-mono flex items-center justify-end gap-1.5">
            <span className="h-2 w-2 rounded-full border border-terra border-t-transparent animate-spin" />
            Loading archive...
          </div>
        )}
      </div>

      {/* Error Toast */}
      {archiveErr && (
        <button
          onClick={() => setArchiveErr(null)}
          className="absolute bottom-20 left-1/2 z-20 max-w-[80%] -translate-x-1/2 truncate rounded-xl border border-coral/50 bg-basalt-100/95 px-5 py-2.5 text-xs text-coral shadow-2xl backdrop-blur font-medium"
        >
          Hotspots error: {archiveErr} <span className="ml-2 text-muted">(Click to dismiss)</span>
        </button>
      )}

      {/* Toast Notification */}
      {mapToast && (
        <button
          onClick={() => setMapToast(null)}
          className="glass-hud absolute bottom-20 left-1/2 z-20 max-w-[80%] -translate-x-1/2 truncate rounded-xl px-5 py-2.5 text-xs text-alabaster shadow-2xl backdrop-blur font-medium"
        >
          {mapToast} <span className="ml-2 text-muted">(Click to dismiss)</span>
        </button>
      )}
    </div>
  );
}

function setDrawBoxSource(map: MlMap, a: [number, number], b: [number, number]) {
  const w = Math.min(a[0], b[0]);
  const es = Math.max(a[0], b[0]);
  const s = Math.min(a[1], b[1]);
  const n = Math.max(a[1], b[1]);
  (map.getSource("draw") as maplibregl.GeoJSONSource).setData({
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [[[w, s], [es, s], [es, n], [w, n], [w, s]]] },
      properties: {},
    }],
  });
}
