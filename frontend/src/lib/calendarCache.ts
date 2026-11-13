/** In-memory calendar payload cache.
 *
 * CalendarView unmounts whenever the user switches to the map or analytics
 * view, so a fetch tied to "component mounted" reloads the same (AOI, year
 * window, filter) payload on every tab switch. The map already keeps its
 * snapshot in a module-level cache; the calendar now does the same, keyed by
 * the exact request signature. The cache lives for the page session and is
 * cleared after an ingest/rebuild that changes the underlying rows.
 */

import type { CalendarResponse } from "../api/types";

export interface CalendarFilterState {
  sensor: string;
  confidence: string;
  daynight: string;
  minFrp: number | null;
  hideStaticSources: boolean;
}

const CACHE_LIMIT = 8;
const cache = new Map<string, CalendarResponse>();

export function calendarCacheKey(
  aoiId: string,
  yearFilter: string,
  filters: CalendarFilterState,
): string {
  return [
    aoiId,
    yearFilter,
    filters.sensor,
    filters.confidence,
    filters.daynight,
    filters.minFrp ?? "",
    filters.hideStaticSources ? "static-off" : "static-on",
  ].join("|");
}

export function getCachedCalendar(key: string): CalendarResponse | undefined {
  return cache.get(key);
}

export function setCachedCalendar(key: string, data: CalendarResponse): void {
  if (cache.size >= CACHE_LIMIT && !cache.has(key)) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
  cache.set(key, data);
}

/** Drop every cached payload after new rows land (live fetch / rebuild). */
export function clearCalendarCache(): void {
  cache.clear();
}