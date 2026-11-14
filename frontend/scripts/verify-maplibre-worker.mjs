#!/usr/bin/env node
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(process.argv[2] ?? path.join(scriptDir, "../dist"));
const assetsDir = path.join(distDir, "assets");
const workerPattern = /maplibre-gl-worker[-\w]*\.mjs/g;

async function findJavaScriptFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await findJavaScriptFiles(entryPath));
    } else if (entry.isFile() && entry.name.endsWith(".js")) {
      files.push(entryPath);
    }
  }
  return files;
}

function fail(message) {
  console.error(`MapLibre worker asset verification failed: ${message}`);
  process.exit(1);
}

let assetsStat;
try {
  assetsStat = await stat(assetsDir);
} catch {
  fail(`assets directory does not exist: ${assetsDir}`);
}
if (!assetsStat.isDirectory()) {
  fail(`assets path is not a directory: ${assetsDir}`);
}

const bundles = await findJavaScriptFiles(assetsDir);
if (bundles.length === 0) {
  fail(`no JavaScript bundles found under ${assetsDir}`);
}

const references = new Map();
for (const bundle of bundles) {
  const source = await readFile(bundle, "utf8");
  for (const match of source.matchAll(workerPattern)) {
    // MapLibre also embeds bare fallback names in its runtime helper. Only a
    // URL path under the emitted assets directory refers to a build asset.
    if (!/(?:^|\/)assets\/$/.test(source.slice(0, match.index))) continue;
    const assetName = match[0];
    const referencedBy = references.get(assetName) ?? new Set();
    referencedBy.add(path.relative(distDir, bundle));
    references.set(assetName, referencedBy);
  }
}

if (references.size === 0) {
  fail(`no ${workerPattern} references found in ${bundles.length} JavaScript bundle(s)`);
}

const missing = [];
for (const [assetName, referencedBy] of references) {
  const assetPath = path.join(assetsDir, assetName);
  let assetStat;
  try {
    assetStat = await stat(assetPath);
  } catch {
    missing.push(`${assetName} (referenced by ${[...referencedBy].join(", ")})`);
    continue;
  }
  if (!assetStat.isFile()) {
    missing.push(`${assetName} is not a file (referenced by ${[...referencedBy].join(", ")})`);
  }
}

if (missing.length > 0) {
  fail(`referenced worker asset(s) missing:\n  ${missing.join("\n  ")}`);
}

console.log(`Verified ${references.size} MapLibre worker asset reference(s) across ${bundles.length} JavaScript bundle(s):`);
for (const [assetName, referencedBy] of references) {
  console.log(`  assets/${assetName} <- ${[...referencedBy].join(", ")}`);
}
