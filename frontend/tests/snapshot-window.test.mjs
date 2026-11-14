import assert from "node:assert/strict";
import test from "node:test";

import {
  cellsForRange,
  countInWindow,
  isoToDayOrd,
  rangeRowBounds,
} from "../src/lib/snapshot.ts";

const FLAG_STATIC = 1 << 1;

function makeSnapshot(inputRows) {
  const rows = [...inputRows].sort((a, b) => a.date.localeCompare(b.date));
  const days = rows.map((row) => isoToDayOrd(row.date));
  const dayValues = [...new Set(days)];
  const dayStart = [];
  const dayEnd = [];
  const cum = [0];
  let cursor = 0;
  for (const day of dayValues) {
    dayStart.push(cursor);
    while (cursor < days.length && days[cursor] === day) cursor++;
    dayEnd.push(cursor);
    cum.push(cursor);
  }
  return {
    header: {},
    n: rows.length,
    day: Uint16Array.from(days),
    lat: Float64Array.from(rows, (row) => row.lat ?? 0),
    lon: Float64Array.from(rows, (row) => row.lon ?? 0),
    acqDay: new Uint16Array(rows.length),
    acqMin: new Uint16Array(rows.length),
    sat: new Uint8Array(rows.length),
    frp: Float64Array.from(rows, (row) => row.frp ?? NaN),
    t4: new Float64Array(rows.length),
    conf: new Uint8Array(rows.length),
    flags: Uint8Array.from(rows, (row) => row.flags ?? 0),
    dayValues: Uint16Array.from(dayValues),
    dayStart: Uint32Array.from(dayStart),
    dayEnd: Uint32Array.from(dayEnd),
    cum: Uint32Array.from(cum),
    dayIso: dayValues.map((day) => new Date(Date.UTC(2000, 0, 1) + day * 86400000)
      .toISOString().slice(0, 10)),
  };
}

function representativeCellRows(count, date, originLat, originLon) {
  const cellDeg = 0.05;
  const latBase = Math.floor(originLat / cellDeg);
  const lonBase = Math.floor(originLon / cellDeg);
  const rows = [];
  for (let i = 0; i < count; i++) {
    const latBin = latBase + Math.floor(i / 30);
    const lonBin = lonBase + (i % 30);
    const lat = (latBin + 0.2) * cellDeg;
    const lon = (lonBin + 0.2) * cellDeg;
    // Two detections in each cell exercise aggregation as well as cell counts.
    rows.push({ date, lat, lon, frp: i + 1 });
    rows.push({ date, lat: lat + 0.001, lon: lon + 0.001, frp: i + 2 });
  }
  return rows;
}

test("rangeRowBounds includes both endpoints and returns half-open row indices", () => {
  const snapshot = makeSnapshot([
    { date: "2024-01-02" },
    { date: "2024-01-02" },
    { date: "2024-01-04" },
    { date: "2024-01-07" },
    { date: "2024-01-07" },
  ]);

  assert.deepEqual(rangeRowBounds(snapshot, "2024-01-02", "2024-01-04"), {
    from: 0,
    to: 3,
  });
  assert.deepEqual(rangeRowBounds(snapshot, "2024-01-07", "2024-01-07"), {
    from: 3,
    to: 5,
  });
});

test("rangeRowBounds handles absent start/end dates and out-of-range endpoints", () => {
  const snapshot = makeSnapshot([
    { date: "2024-01-02" },
    { date: "2024-01-02" },
    { date: "2024-01-04" },
    { date: "2024-01-07" },
  ]);

  // Neither boundary exists in dayValues; the range selects the intervening day.
  assert.deepEqual(rangeRowBounds(snapshot, "2024-01-03", "2024-01-06"), {
    from: 2,
    to: 3,
  });
  assert.deepEqual(rangeRowBounds(snapshot, "2023-12-31", "2024-01-08"), {
    from: 0,
    to: 4,
  });
  assert.deepEqual(rangeRowBounds(snapshot, "2024-01-03", "2024-01-03"), {
    from: 2,
    to: 2,
  });
});

test("cellsForRange rejects non-positive or non-finite cell sizes", () => {
  const snapshot = makeSnapshot([{ date: "2024-01-01", lat: 1, lon: 2 }]);
  for (const cellDeg of [0, -0.05, NaN, Infinity, -Infinity]) {
    assert.throws(
      () => cellsForRange(snapshot, "2024-01-01", "2024-01-01", false, cellDeg),
      { name: "RangeError", message: "cellDeg must be a positive finite number" },
    );
  }
});

test("cellsForRange filters static rows and ignores rows outside the selected slice", () => {
  const snapshot = makeSnapshot([
    { date: "2024-01-01", lat: 30.01, lon: 31.01, frp: 2 },
    { date: "2024-01-01", lat: 30.02, lon: 31.02, frp: 10, flags: FLAG_STATIC },
    { date: "2024-01-01", lat: 30.11, lon: 31.11, frp: 5, flags: FLAG_STATIC },
    { date: "2024-01-02", lat: 10.01, lon: 10.01, frp: 1000 },
  ]);

  const visible = cellsForRange(snapshot, "2024-01-01", "2024-01-01", false);
  const filtered = cellsForRange(snapshot, "2024-01-01", "2024-01-01", true);
  assert.equal(visible.length, 2);
  assert.equal(visible[0].properties.n, 2);
  assert.equal(filtered.length, 1);
  assert.equal(filtered[0].properties.n, 1);
  assert.equal(filtered[0].properties.max_frp_mw, 2);
  assert.equal(countInWindow(snapshot, "2024-01-01", "2024-01-01"), 3);
  assert.equal(countInWindow(snapshot, "2024-01-01", "2024-01-01", true), 1);
});

test("cellsForRange reports maximum non-missing FRP and zero when all FRP is missing", () => {
  const snapshot = makeSnapshot([
    { date: "2024-02-01", lat: 12.01, lon: 44.01, frp: NaN },
    { date: "2024-02-01", lat: 12.02, lon: 44.02, frp: 3.5 },
    { date: "2024-02-01", lat: 12.03, lon: 44.03, frp: 2 },
    { date: "2024-02-01", lat: 12.11, lon: 44.11, frp: NaN },
    { date: "2024-02-01", lat: 12.12, lon: 44.12, frp: NaN },
  ]);

  const cells = cellsForRange(snapshot, "2024-02-01", "2024-02-01", false);
  assert.equal(cells.length, 2);
  assert.equal(cells[0].properties.n, 3);
  assert.equal(cells[0].properties.max_frp_mw, 3.5);
  assert.equal(cells[1].properties.n, 2);
  assert.equal(cells[1].properties.max_frp_mw, 0);
});

test("cell Point and properties use the degree-cell center", () => {
  const snapshot = makeSnapshot([
    { date: "2024-03-01", lat: 0.049, lon: -0.001, frp: 1 },
  ]);
  const [cell] = cellsForRange(snapshot, "2024-03-01", "2024-03-01", false);

  assert.deepEqual(cell.geometry, { type: "Point", coordinates: [-0.025, 0.025] });
  assert.equal(cell.properties.lat, 0.025);
  assert.equal(cell.properties.lon, -0.025);
});

test("latitude/longitude bin pairs use collision-safe keys", () => {
  const snapshot = makeSnapshot([
    { date: "2024-04-01", lat: 1.1, lon: 23.1, frp: 1 },
    { date: "2024-04-01", lat: 12.1, lon: 3.1, frp: 2 },
  ]);

  assert.equal(cellsForRange(snapshot, "2024-04-01", "2024-04-01", false, 1).length, 2);
});

test("0.05-degree aggregation emits representative Nile and Punjab cell totals", () => {
  const snapshot = makeSnapshot([
    ...representativeCellRows(551, "2024-05-01", 29.9, 31.2),
    ...representativeCellRows(259, "2024-05-02", 31.0, 73.8),
    ...representativeCellRows(590, "2024-05-03", 31.0, 73.8),
  ]);

  assert.equal(cellsForRange(snapshot, "2024-05-01", "2024-05-01", false).length, 551);
  assert.equal(cellsForRange(snapshot, "2024-05-02", "2024-05-02", false).length, 259);
  assert.equal(cellsForRange(snapshot, "2024-05-03", "2024-05-03", false).length, 590);
});
