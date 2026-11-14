/**
 * Columnar snapshot decoder (v1.0.6, handoff P1b).
 *
 * Wire format (after gunzip):
 *   EMBSNAP1 | u32le header_len | header JSON (utf-8) | n x 21-byte records
 *
 * Record (little-endian, 21 bytes, no padding: <HiiHHBHhBB):
 *   day      u16  offset 0  - days since 2000-01-01 (local_date; sorted asc)
 *   lat      i32  offset 2  - round(lat * 1e5)
 *   lon      i32  offset 6  - round(lon * 1e5)
 *   acq_day  u16  offset 10 - days since 2000-01-01 (UTC acquisition date)
 *   acq_min  u16  offset 12 - minutes of the UTC acquisition day
 *   sat      u8   offset 14 - index into SAT_ORDER (255 = other)
 *   frp      u16  offset 15 - round(frp_mw * 10); 65535 = null
 *   t4       i16  offset 17 - round((t4_k - 200) * 10); -32768 = null
 *   conf     u8   offset 19 - 0 LOW, 1 NOMINAL, 2 HIGH, 3 other
 *   flags    u8   offset 20 - 1 night, 2 static, 4 saturated, 8 band21, 16 SP, 32 NRT
 *
 * Rows arrive sorted by day, so a single pass builds an O(1) per-day offset
 * index: any playback day is a typed-array slice, ZERO network requests.
 */
import type { ConfidenceClass, HotspotFeature } from "../api/types";

export const SNAP_MAGIC = 0x454d4253; // "EMBS" (le read of "EMBS")
const MAGIC_TXT = "EMBSNAP1";
export const SAT_ORDER = ["Terra", "Aqua", "SNPP", "N20", "N21"] as const;
const DAY0_ORD = Date.UTC(2000, 0, 1) / 86400000;

const FLAG_NIGHT = 1 << 0;
const FLAG_STATIC = 1 << 1;
const FLAG_SATURATED = 1 << 2;
const FLAG_BAND21 = 1 << 3;
const FLAG_SP = 1 << 4;
const FLAG_NRT = 1 << 5;

export interface SnapshotHeader {
  mode: "snapshot";
  n: number;
  stride: number;
  fields: string[];
  day0: string;
  day_min: string | null;
  day_max: string | null;
  sat_order: string[];
}

export interface SnapshotData {
  header: SnapshotHeader;
  n: number;
  /** per-row columns */
  day: Uint16Array;
  lat: Float64Array;
  lon: Float64Array;
  acqDay: Uint16Array;
  acqMin: Uint16Array;
  sat: Uint8Array;
  frp: Float64Array;
  t4: Float64Array;
  conf: Uint8Array;
  flags: Uint8Array;
  /** day offset index: sorted unique day values + [start,end) per day */
  dayValues: Uint16Array;
  dayStart: Uint32Array;
  dayEnd: Uint32Array;
  /** cumulative row count up to each dayValue (length dayValues.length+1) */
  cum: Uint32Array;
  /** ISO strings cache */
  dayIso: string[];
}

function gunzip(buf: ArrayBuffer): Promise<ArrayBuffer> {
  if (typeof DecompressionStream === "undefined") {
    return Promise.reject(
      new Error("This browser cannot decode the archive payload (no DecompressionStream)."),
    );
  }
  const ds = new DecompressionStream("gzip");
  return new Response(new Blob([buf]).stream().pipeThrough(ds)).arrayBuffer();
}

function ordToIso(day: number): string {
  return new Date(DAY0_ORD * 86400000 + day * 86400000).toISOString().slice(0, 10);
}

export function isoToDayOrd(iso: string): number {
  return Math.round(Date.parse(`${iso}T00:00:00Z`) / 86400000) - DAY0_ORD;
}

export async function decodeSnapshot(buf: ArrayBuffer): Promise<SnapshotData> {
  const raw = await gunzip(buf);
  const magic = new TextDecoder().decode(raw.slice(0, 8));
  if (magic !== MAGIC_TXT) throw new Error("Bad snapshot payload (magic mismatch)");
  const dv = new DataView(raw);
  const hlen = dv.getUint32(8, true);
  const header = JSON.parse(new TextDecoder().decode(
    raw.slice(12, 12 + hlen))) as SnapshotHeader;
  const n = header.n;
  const base = 12 + hlen;
  const stride = header.stride;

  const day = new Uint16Array(n);
  const lat = new Float64Array(n);
  const lon = new Float64Array(n);
  const acqDay = new Uint16Array(n);
  const acqMin = new Uint16Array(n);
  const sat = new Uint8Array(n);
  const frp = new Float64Array(n);
  const t4 = new Float64Array(n);
  const conf = new Uint8Array(n);
  const flags = new Uint8Array(n);

  let off = base;
  for (let i = 0; i < n; i++, off += stride) {
    day[i] = dv.getUint16(off, true);
    lat[i] = dv.getInt32(off + 2, true) / 1e5;
    lon[i] = dv.getInt32(off + 6, true) / 1e5;
    acqDay[i] = dv.getUint16(off + 10, true);
    acqMin[i] = dv.getUint16(off + 12, true);
    sat[i] = dv.getUint8(off + 14);
    const f = dv.getUint16(off + 15, true);
    frp[i] = f === 65535 ? NaN : f / 10;
    const t = dv.getInt16(off + 17, true);
    t4[i] = t === -32768 ? NaN : 200 + t / 10;
    conf[i] = dv.getUint8(off + 19);
    flags[i] = dv.getUint8(off + 20);
  }

  // ---- per-day offset index (single pass; rows sorted by day) ----
  const dayValues: number[] = [];
  const dayStart: number[] = [];
  const dayEnd: number[] = [];
  let prev = -1;
  for (let i = 0; i < n; i++) {
    if (day[i] !== prev) {
      if (dayValues.length) dayEnd.push(i); // close the previous day
      dayValues.push(day[i]);
      dayStart.push(i);
      prev = day[i];
    }
  }
  if (dayValues.length) dayEnd.push(n);
  const cum = new Uint32Array(dayValues.length + 1);
  for (let k = 0; k < dayValues.length; k++) cum[k + 1] = dayEnd[k];

  return {
    header, n, day, lat, lon, acqDay, acqMin, sat, frp, t4, conf, flags,
    dayValues: new Uint16Array(dayValues),
    dayStart: new Uint32Array(dayStart),
    dayEnd: new Uint32Array(dayEnd),
    cum,
    dayIso: dayValues.map(ordToIso),
  };
}

function daySlice(s: SnapshotData, dayOrd: number): { from: number; to: number } {
  // binary search over dayValues
  let lo = 0, hi = s.dayValues.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (s.dayValues[mid] === dayOrd) { found = mid; break; }
    if (s.dayValues[mid] < dayOrd) lo = mid + 1; else hi = mid - 1;
  }
  if (found < 0) return { from: 0, to: 0 };
  return { from: s.dayStart[found], to: s.dayEnd[found] };
}

/** Row indices of one local day. */
export function dayRowRange(s: SnapshotData, dayIso: string): { from: number; to: number } {
  return daySlice(s, isoToDayOrd(dayIso));
}

/** Row indices for the inclusive local-date window [startIso, endIso]. */
export function rangeRowBounds(
  s: SnapshotData, startIso: string, endIso: string,
): { from: number; to: number } {
  const startDay = isoToDayOrd(startIso), endDay = isoToDayOrd(endIso);
  const lowerBound = (dayOrd: number): number => {
    let lo = 0, hi = s.dayValues.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (s.dayValues[mid] < dayOrd) lo = mid + 1; else hi = mid;
    }
    return lo;
  };
  const firstDay = lowerBound(startDay);
  // Searching from firstDay keeps reversed windows empty rather than returning
  // a backwards slice, matching countInWindow's historical behavior.
  let lo = firstDay, hi = s.dayValues.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (s.dayValues[mid] <= endDay) lo = mid + 1; else hi = mid;
  }
  return { from: s.cum[firstDay], to: s.cum[lo] };
}

/** Count rows whose local_date lies within [startIso, endIso] inclusive. */
export function countInWindow(
  s: SnapshotData, startIso: string, endIso: string, hideStatic = false,
): number {
  const { from, to } = rangeRowBounds(s, startIso, endIso);
  if (!hideStatic) return to - from;
  let count = 0;
  for (let i = from; i < to; i++) {
    if (!(s.flags[i] & FLAG_STATIC)) count++;
  }
  return count;
}

const CONF_NAMES = ["LOW", "NOMINAL", "HIGH", "N/A"] as const;
function acqIso(day: number, min: number): string {
  const d = ordToIso(day);
  return `${d}T${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
}

/** Build GeoJSON features for one playback day (optionally hiding static
 * sources client-side - the flag travels in the snapshot, no refetch). */
export function featuresForDay(
  s: SnapshotData, dayIso: string, hideStatic: boolean,
): HotspotFeature[] {
  const { from, to } = daySlice(s, isoToDayOrd(dayIso));
  const out: HotspotFeature[] = [];
  for (let i = from; i < to; i++) {
    if (hideStatic && (s.flags[i] & FLAG_STATIC)) continue;
    const satName = SAT_ORDER[s.sat[i]] ?? "unknown";
    const fl = s.flags[i];
    out.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [s.lon[i], s.lat[i]] },
      properties: {
        id: i,
        sensor: satName === "Terra" || satName === "Aqua" ? "MODIS" : "VIIRS",
        satellite: satName,
        acq_dt_utc: acqIso(s.acqDay[i], s.acqMin[i]),
        local_date: dayIso,
        frp_mw: Number.isNaN(s.frp[i]) ? null : s.frp[i],
        t4_k: Number.isNaN(s.t4[i]) ? null : s.t4[i],
        t11_k: null,
        confidence: CONF_NAMES[s.conf[i]] as ConfidenceClass,
        daynight: fl & FLAG_NIGHT ? "N" : "D",
        pixel_area_km2: satName === "Terra" || satName === "Aqua" ? 1 : 0.14,
        saturated: !!(fl & FLAG_SATURATED),
        band21_fallback: !!(fl & FLAG_BAND21),
        static_source: !!(fl & FLAG_STATIC),
        firms_product: fl & FLAG_SP ? "SP" : fl & FLAG_NRT ? "NRT" : null,
        firms_type: null,
      },
    });
  }
  return out;
}

export interface CellFeature {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: { n: number; max_frp_mw: number; lat: number; lon: number };
}

/** Aggregate observations in an inclusive date window to degree-grid cells. */
export function cellsForRange(
  s: SnapshotData,
  startIso: string,
  endIso: string,
  hideStatic: boolean,
  cellDeg = 0.05,
): CellFeature[] {
  if (!Number.isFinite(cellDeg) || cellDeg <= 0) {
    throw new RangeError("cellDeg must be a positive finite number");
  }
  const { from, to } = rangeRowBounds(s, startIso, endIso);
  const cells = new Map<string, CellFeature>();
  for (let i = from; i < to; i++) {
    if (hideStatic && (s.flags[i] & FLAG_STATIC)) continue;
    const latBin = Math.floor(s.lat[i] / cellDeg);
    const lonBin = Math.floor(s.lon[i] / cellDeg);
    // The separator makes signed latitude/longitude bin pairs unambiguous.
    const key = `${latBin},${lonBin}`;
    let feature = cells.get(key);
    if (!feature) {
      const lat = (latBin + 0.5) * cellDeg;
      const lon = (lonBin + 0.5) * cellDeg;
      feature = {
        type: "Feature",
        geometry: { type: "Point", coordinates: [lon, lat] },
        properties: { n: 0, max_frp_mw: NaN, lat, lon },
      };
      cells.set(key, feature);
    }
    feature.properties.n++;
    const frp = s.frp[i];
    if (!Number.isNaN(frp) &&
        (Number.isNaN(feature.properties.max_frp_mw) || feature.properties.max_frp_mw < frp)) {
      feature.properties.max_frp_mw = frp;
    }
  }
  for (const feature of cells.values()) {
    if (Number.isNaN(feature.properties.max_frp_mw)) feature.properties.max_frp_mw = 0;
  }
  return Array.from(cells.values());
}
