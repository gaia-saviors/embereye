/** Color system: Warm Basalt + Scientific Magma palette.
 * All sensor identities and UI colors updated for the new design system. */

/** 6-stop Perceptual Magma Ramp (0 fires → extreme p99) */
export const EMBER_RAMP = [
  "#16141C", // Stop 0: 0 fires
  "#3B182E", // Stop 1: Low — Deep Plum
  "#7A1F34", // Stop 2: Moderate — Burgundy
  "#C83E2B", // Stop 3: High — Terracotta
  "#F08C2E", // Stop 4: Very High — Solar Gold
  "#FFF3B0", // Stop 5: Extreme p99 — Incandescent Ivory
];

/** 6-stop Perceptually Uniform Viridis Ramp (Accessible: Deuteranopia / Protanopia safe) */
export const VIRIDIS_RAMP = [
  "#16141C", // Stop 0: 0 fires
  "#440154", // Deep Violet
  "#3B528B", // Blue-Indigo
  "#21918C", // Ocean Teal
  "#5EC962", // Emerald Green
  "#FDE725", // Luminous Yellow
];

/** 6-stop Cividis Ramp (Formally Optimized for Deuteranopia & Protanopia Red-Green Blindness) */
export const CIVIDIS_RAMP = [
  "#16141C", // Stop 0: 0 fires
  "#00204D", // Deep Navy
  "#414D6B", // Slate Steel
  "#7C7B78", // Pewter
  "#BFAF6F", // Ochre Sand
  "#FFEA46", // Bright Gold
];

export type ColorPalette = "magma" | "viridis" | "cividis";

export const PALETTES: Record<ColorPalette, string[]> = {
  magma: EMBER_RAMP,
  viridis: VIRIDIS_RAMP,
  cividis: CIVIDIS_RAMP,
};

export function rampColor(t: number, palette: ColorPalette = "magma"): string {
  const ramp = PALETTES[palette] || EMBER_RAMP;
  // Defensive: any non-finite input maps to the cold end, never a crash.
  if (typeof t !== "number" || !isFinite(t)) return ramp[0];
  const x = Math.max(0, Math.min(1, t));
  const scaled = x * (ramp.length - 1);
  const i = Math.floor(scaled);
  const f = scaled - i;
  if (i >= ramp.length - 1) return ramp[ramp.length - 1];
  const a = ramp[i];
  const b = ramp[i + 1];
  return lerpHex(a, b, f);
}

function lerpHex(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const r = Math.round(((pa >> 16) & 255) * (1 - t) + ((pb >> 16) & 255) * t);
  const g = Math.round(((pa >> 8) & 255) * (1 - t) + ((pb >> 8) & 255) * t);
  const bl = Math.round((pa & 255) * (1 - t) + (pb & 255) * t);
  return `#${((r << 16) | (g << 8) | bl).toString(16).padStart(6, "0")}`;
}

/** Sensor identity colors */
export const SENSOR_COLOR: Record<string, string> = {
  MODIS: "#E5A93C",  // Champagne Amber
  VIIRS: "#E04F33",  // Terracotta Rose
};

export const CONFIDENCE_COLOR: Record<string, string> = {
  LOW: "#686472",
  NOMINAL: "#E5A93C",
  HIGH: "#E04F33",
};

/** Anomaly channel — still colorblind-safe (blue-based, opposite the magma ramp) */
export const ANOMALY_HIGH = "#F43F5E"; // Crimson Coral (p>=95)
export const ANOMALY_LOW = "#2DD4BF";  // Teal Mist (p<=5)

export const UI = {
  bg: "#0C0B0E",
  panel: "#141318",
  border: "#25232C",
  text: "#F4F3F6",
  muted: "#A09CA8",
};
