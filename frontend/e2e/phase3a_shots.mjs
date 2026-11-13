/**
 * Phase 3A verification screenshots (defects 1/3/4/6/7/8/9/10).
 *
 * Robust replacement pass for the P2e screenshots: waits for real data
 * (snapshot card text on the map, headline card on analytics) instead of
 * fixed timeouts, and never lets one slow view kill the whole run.
 *
 *   EMBEREYE_URL      app base URL (default http://127.0.0.1:4173)
 *   EMBEREYE_SHOT_DIR output dir
 */
import { mkdirSync } from "node:fs";
import path from "node:path";

const BASE = process.env.EMBEREYE_URL ?? "http://127.0.0.1:4173";
const SHOT_DIR = process.env.EMBEREYE_SHOT_DIR ?? path.resolve("e2e/screenshots");
mkdirSync(SHOT_DIR, { recursive: true });

const { chromium } = await import("playwright");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(20000);

async function shot(name) {
  await page.screenshot({ path: path.join(SHOT_DIR, name) });
  console.log("[shots] saved", name);
}

async function clickNav(label) {
  await page.getByRole("button", { name: label, exact: true }).first().click();
}

try {
  console.log("[shots] goto", BASE);
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });
  // wait for the badge to show the real data source (defect 1)
  await page
    .waitForFunction(
      () => document.body.innerText.includes("NASA FIRMS archive (real data)"),
      undefined,
      { timeout: 30000 },
    )
    .catch(() => console.log("[shots] WARN badge text not seen"));

  // CALENDAR 1440 - wait for the year rows to render (2000 row present)
  await clickNav("CALENDAR");
  await page.waitForFunction(
    () => document.body.innerText.includes("week scale"),
    undefined,
    { timeout: 30000 },
  ).catch(() => {});
  await page.waitForTimeout(1500);
  await shot("calendar-1440.png");

  // MAP 1440 - wait for the snapshot card ("whole record loaded")
  await clickNav("MAP");
  await page
    .waitForFunction(
      () => document.body.innerText.includes("whole record loaded"),
      undefined,
      { timeout: 45000 },
    )
    .then(() => console.log("[shots] snapshot loaded"))
    .catch(() => console.log("[shots] WARN snapshot card not seen"));
  await page.waitForTimeout(2500);
  await shot("map-1440.png");

  // ANALYTICS 1440 - wait for the headline card (LAST COMPLETE MONTH)
  await clickNav("ANALYTICS");
  await page
    .waitForFunction(
      () => document.body.innerText.includes("LAST COMPLETE MONTH"),
      undefined,
      { timeout: 45000 },
    )
    .catch(() => console.log("[shots] WARN headline not seen"));
  await page.waitForTimeout(2000);
  await shot("analytics-1440.png");

  // 390px pass
  await page.setViewportSize({ width: 390, height: 844 });
  await clickNav("CALENDAR");
  await page.waitForTimeout(2500);
  await shot("calendar-390.png");

  await clickNav("MAP");
  await page
    .waitForFunction(
      () => document.body.innerText.includes("whole record loaded"),
      undefined,
      { timeout: 45000 },
    )
    .catch(() => {});
  await page.waitForTimeout(2500);
  await shot("map-390.png");

  await clickNav("ANALYTICS");
  await page.waitForTimeout(3000);
  await shot("analytics-390.png");

  console.log("[shots] DONE");
} catch (e) {
  console.error("[shots] FAILED:", e.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
