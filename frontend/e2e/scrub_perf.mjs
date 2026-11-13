/**
 * EmberEye v1.0.6 automated verification (handoff P1e + P2e).
 *
 * P1e  scrubbing performance: 100 slider moves must produce <= 2 API
 *      requests and 0 failed requests, with frame/long-task statistics.
 * P2e  rendered screenshots of Calendar / Map / Analytics at 1440px and
 *      390px (visual check: Y axes >= 0, climatology band p10-p90).
 *
 * Run (one-time setup):
 *   npm i -D playwright && npx playwright install chromium
 *   npm run test:e2e            # against a stack on :8000 backend / :4173 preview
 *
 * Environment:
 *   EMBEREYE_URL      app base URL (default http://127.0.0.1:4173)
 *   EMBEREYE_SHOT_DIR where screenshots go (default ./e2e/screenshots)
 *   PW_INSTALL_DIR    dir containing node_modules/playwright, if playwright
 *                     is not resolvable from this file
 *
 * Exit code 0 = all assertions passed.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";

const BASE = process.env.EMBEREYE_URL ?? "http://127.0.0.1:4173";
const SHOT_DIR = process.env.EMBEREYE_SHOT_DIR ?? path.resolve("e2e/screenshots");

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const dir = process.env.PW_INSTALL_DIR;
    if (!dir) throw new Error("playwright not found: npm i -D playwright");
    return await import(pathToFileURL(
      path.join(dir, "node_modules", "playwright", "index.mjs")).href);
  }
}

const { chromium } = await loadPlaywright();

mkdirSync(SHOT_DIR, { recursive: true });

// ---- instrument a fresh page ------------------------------------------------
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const apiRequests = [];   // {url, ts}
const apiFailures = [];   // {url, err|status}
let baselineCount = 0;

page.on("request", (r) => {
  if (r.url().includes("/api/")) apiRequests.push({ url: r.url(), ts: Date.now() });
});
page.on("requestfailed", (r) => {
  if (r.url().includes("/api/")) {
    apiFailures.push({ url: r.url(), err: r.failure()?.errorText ?? "failed" });
  }
});
page.on("response", (r) => {
  if (r.url().includes("/api/") && r.status() >= 400) {
    apiFailures.push({ url: r.url(), err: `HTTP ${r.status()}` });
  }
});

console.log("[e2e] goto", BASE);
await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });
console.log("[e2e] loaded");

// ---- P2e: CALENDAR screenshot (initial view) --------------------------------
console.log("[e2e] calendar view");
await page.getByRole("button", { name: "CALENDAR" }).click();
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(SHOT_DIR, "calendar-1440.png") });

// ---- MAP + snapshot load -----------------------------------------------------
console.log("[e2e] map view");
await page.getByRole("button", { name: "MAP", exact: true }).click();
await page.waitForTimeout(6000); // snapshot load + first paint
console.log("[e2e] map loaded, snapshot expected");
await page.screenshot({ path: path.join(SHOT_DIR, "map-1440.png") });

// ---- P1e: frame-time + long-task probe ---------------------------------------
await page.evaluate(() => {
  window.__frames = [];
  window.__longTasks = [];
  let last = performance.now();
  const loop = (t) => {
    window.__frames.push(t - last);
    last = t;
    if (!window.__stopFrames) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  try {
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) window.__longTasks.push(e.duration);
    }).observe({ entryTypes: ["longtask"] });
  } catch { /* longtask not supported */ }
});

baselineCount = apiRequests.length;

// ---- 100 slider moves ---------------------------------------------------------
const slider = page.locator('input[aria-label="timeline cursor"]');
console.log("[e2e] scrubbing 100 steps...");
await slider.focus();
await slider.press("Home"); // jump to range start
const scrubStart = Date.now();
for (let i = 0; i < 100; i++) {
  await slider.press("ArrowRight");
  await page.waitForTimeout(25);
}
const scrubMs = Date.now() - scrubStart;

const during = apiRequests.slice(baselineCount);

console.log("[e2e] scrub done, collecting stats");
const stats = await page.evaluate(() => {
  window.__stopFrames = true;
  const fr = window.__frames ?? [];
  const lt = window.__longTasks ?? [];
  const mem = performance.memory
    ? Math.round(performance.memory.usedJSHeapSize / 1048576)
    : null;
  return {
    frames: fr.length,
    avgFrameMs: fr.length ? Math.round(fr.reduce((a, b) => a + b, 0) / fr.length) : null,
    maxFrameMs: fr.length ? Math.round(Math.max(...fr)) : null,
    longTasks: lt.length,
    maxLongTaskMs: lt.length ? Math.round(Math.max(...lt)) : null,
    heapMB: mem,
  };
});

// ---- ANALYTICS screenshots ----------------------------------------------------
console.log("[e2e] analytics view");
await page.getByRole("button", { name: "ANALYTICS" }).click();
await page.waitForTimeout(3000);
await page.screenshot({ path: path.join(SHOT_DIR, "analytics-1440.png") });

// ---- 390px pass ----------------------------------------------------------------
await page.setViewportSize({ width: 390, height: 844 });
console.log("[e2e] 390px calendar");
await page.getByRole("button", { name: "CALENDAR" }).click();
await page.waitForTimeout(2000);
await page.screenshot({ path: path.join(SHOT_DIR, "calendar-390.png") });
await page.getByRole("button", { name: "MAP", exact: true }).click();
await page.waitForTimeout(3000);
await page.screenshot({ path: path.join(SHOT_DIR, "map-390.png") });
await page.getByRole("button", { name: "ANALYTICS" }).click();
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(SHOT_DIR, "analytics-390.png") });

// ---- report --------------------------------------------------------------------
const report = {
  scrub: {
    sliderMoves: 100,
    apiRequestsDuring: during.length,
    apiRequestUrlsDuring: [...new Set(during.map((d) => d.url.split("?")[0]))],
    apiFailuresTotal: apiFailures.length,
    scrubWallMs: scrubMs,
    ...stats,
  },
  totals: {
    apiRequestsWholeRun: apiRequests.length,
    apiFailuresWholeRun: apiFailures.length,
  },
};
writeFileSync(path.join(SHOT_DIR, "scrub_report.json"),
              JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));

// ---- assertions (P1e) -----------------------------------------------------------
let ok = true;
if (during.length > 2) {
  console.error(`FAIL: ${during.length} API requests during 100 slider moves (> 2)`);
  ok = false;
}
if (apiFailures.length > 0) {
  console.error(`FAIL: ${apiFailures.length} failed API requests`, apiFailures.slice(0, 5));
  ok = false;
}
await browser.close();
process.exit(ok ? 0 : 1);
