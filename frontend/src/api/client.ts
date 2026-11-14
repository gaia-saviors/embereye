/** Typed API client. Relative paths only - nginx proxies /api in production,
 * vite dev-server proxies it in development. */

import type {
  Aoi,
  CalendarResponse,
  ClimatologyResponse,
  EpisodeOut,
  GridResponse,
  HeadlineResponse,
  HealthResponse,
  HotspotCollection,
  JobOut,
  LiveFetchResponse,
  MetaResponse,
  MethodsResponse,
  SeasonOut,
  SnapshotRedirect,
  SummaryResponse,
  TimeseriesPoint,
} from "./types";

import { decodeSnapshot, type SnapshotData } from "../lib/snapshot";

const BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export interface ExportDownload {
  blob: Blob;
  filename: string;
  truncated: boolean;
  total: number | null;
  limit: number | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, {
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch (err) {
    throw new ApiError(0, `Network unreachable: ${(err as Error).message}`);
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(resp.status, detail);
  }
  // 204/205 have no body by spec (e.g. DELETE /aois). Reading .json() on an
  // empty stream throws and surfaces as a network error - return undefined.
  if (resp.status === 204 || resp.status === 205) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

const qs = (params: Record<string, string | number | boolean | undefined | null>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export const api = {
  health: () => request<HealthResponse>("/health"),
  meta: () => request<MetaResponse>("/meta"),

  listAois: () => request<Aoi[]>("/aois"),
  getAoi: (id: string) => request<Aoi>(`/aois/${id}`),
  createAoi: (name: string, description: string, geojson: unknown) =>
    request<Aoi>("/aois", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, geojson }),
    }),
  deleteAoi: (id: string) => request<void>(`/aois/${id}`, { method: "DELETE" }),
  rebuildAoi: (id: string) => request<JobOut>(`/aois/${id}/rebuild`, { method: "POST" }),
  job: (id: string) => request<JobOut>(`/jobs/${id}`),

  hotspots: (params: {
    aoi: string;
    start?: string;
    end?: string;
    sensor?: string;
    satellite?: string;
    confidence?: string;
    daynight?: string;
    min_frp?: number;
    hide_static?: boolean;
    limit?: number;
    offset?: number;
    bbox?: string;
  }, signal?: AbortSignal) =>
    request<HotspotCollection>(`/hotspots${qs(params)}`, { signal }),

  /** v1.0.6: one compact columnar snapshot per (AOI, attribute filters).
   * Returns parsed typed-array data, or a JSON redirect (mode="grid")
   * when the AOI exceeds the server snapshot cap. The browser HTTP cache
   * revalidates the ETag transparently (304 => cached body). */
  snapshot: async (params: {
    aoi: string;
    sensor?: string;
    satellite?: string;
    confidence?: string;
    daynight?: string;
    min_frp?: number;
  }): Promise<SnapshotData | SnapshotRedirect> => {
    const resp = await fetch(`${BASE}/hotspots/snapshot${qs(params)}`, {
      headers: { Accept: "application/octet-stream, application/json" },
    });
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        detail = (await resp.json()).detail ?? detail;
      } catch { /* keep statusText */ }
      throw new ApiError(resp.status, detail);
    }
    const ct = resp.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) {
      return (await resp.json()) as SnapshotRedirect;
    }
    return decodeSnapshot(await resp.arrayBuffer());
  },

  /** v1.0.6: server-side grid x week|month aggregation for large AOIs. */
  grid: (params: {
    aoi: string;
    res?: "week" | "month";
    start?: string;
    end?: string;
    cell_deg?: number;
    sensor?: string;
    satellite?: string;
    confidence?: string;
    daynight?: string;
    min_frp?: number;
    hide_static?: boolean;
  }) => request<GridResponse>(`/hotspots/grid${qs(params)}`),

  calendar: (
    aoi: string,
    opts: {
      start?: string;
      end?: string;
      startYear?: number;
      endYear?: number;
      compact?: boolean;
      sensor?: string;
      satellite?: string;
      confidence?: string;
      daynight?: string;
      minFrp?: number | null;
      hideStatic?: boolean;
    } = {},
  ) =>
    request<CalendarResponse>(
      `/calendar${qs({
        aoi,
        start: opts.start,
        end: opts.end,
        start_year: opts.startYear,
        end_year: opts.endYear,
        compact: opts.compact ? true : undefined,
        sensor: opts.sensor,
        satellite: opts.satellite,
        confidence: opts.confidence,
        daynight: opts.daynight,
        min_frp: opts.minFrp ?? undefined,
        hide_static: opts.hideStatic ? true : undefined,
      })}`
    ),

  summary: (aoi: string, start?: string, end?: string, includeLayers?: boolean) =>
    request<SummaryResponse>(`/analytics/summary${qs({ aoi, start, end, include_layers: includeLayers })}`),
  headline: (aoi: string) =>
    request<HeadlineResponse>(`/analytics/headline${qs({ aoi })}`),
  timeseries: (aoi: string, start?: string, end?: string, res?: string) =>
    request<TimeseriesPoint[]>(`/analytics/timeseries${qs({ aoi, start, end, res })}`),
  climatology: (aoi: string) => request<ClimatologyResponse>(`/analytics/climatology${qs({ aoi })}`),
  seasons: (aoi: string) => request<SeasonOut[]>(`/analytics/seasons${qs({ aoi })}`),
  episodes: (aoi: string, onlyCritical: boolean, limit = 100) =>
    request<EpisodeOut[]>(`/analytics/episodes${qs({ aoi, only_critical: onlyCritical ? 1 : undefined, limit })}`),
  methods: (aoi: string) => request<MethodsResponse>(`/analytics/methods${qs({ aoi })}`),

  liveFetch: (aoiId: string, dayRange = 1) =>
    request<LiveFetchResponse>("/live/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ aoi_id: aoiId, day_range: dayRange }),
    }),

  exportUrl: (params: {
    aoi: string;
    level: string;
    format: string;
    start?: string;
    end?: string;
    sensor?: string;
    confidence?: string;
    daynight?: string;
    min_frp?: number;
    hide_static?: boolean;
  }): string => `${BASE}/export${qs(params)}`,

  exportDownload: async (params: {
    aoi: string;
    level: string;
    format: string;
    start?: string;
    end?: string;
    sensor?: string;
    confidence?: string;
    daynight?: string;
    min_frp?: number;
    hide_static?: boolean;
  }): Promise<ExportDownload> => {
    const response = await fetch(`${BASE}/export${qs(params)}`, { headers: { Accept: "text/csv, application/geo+json, application/json" } });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail ?? detail; } catch { /* retain status text */ }
      throw new ApiError(response.status, detail);
    }
    const disposition = response.headers.get("content-disposition") ?? "";
    const filenameHeader = disposition.match(/filename\*?=(?:UTF-8''|\")?([^;\"]+)/i)?.[1];
    const numberHeader = (name: string) => {
      const value = response.headers.get(name);
      if (value == null || value.trim() === "") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const truncatedHeader = response.headers.get("X-Result-Truncated")?.toLowerCase();
    return {
      blob: await response.blob(),
      filename: filenameHeader ? decodeURIComponent(filenameHeader.trim()) : `embereye-${params.level}.${params.format}`,
      truncated: truncatedHeader === "true" || truncatedHeader === "1" || truncatedHeader === "yes",
      total: numberHeader("X-Result-Total"),
      limit: numberHeader("X-Result-Limit"),
    };
  },
};
