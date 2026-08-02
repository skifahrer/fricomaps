#!/usr/bin/env node
/**
 * Pripraví PBF exporty regiónov tak, ako sa robia oficiálne OSM exporty:
 * z GeoJSON-u administratívnych hraníc (osmium export) vyberie polygón
 * každého kraja a vygeneruje:
 *
 *   <out>/poly/{region}.geojson   – hraničné polygóny pre osmium
 *   <out>/config.json             – konfig pre jednoprechodový `osmium extract -c`
 *   <out>/meta.json               – meno + presný bbox každého regiónu
 *
 * Použitie:
 *   node workers/prepare-extracts.mjs --admin=data/admin.geojson --out=data/extracts
 */
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.join("=")];
  })
);

const adminFile = args.admin;
const outDir = args.out || "data/extracts";
if (!adminFile) {
  console.error("Chýba --admin=<admin.geojson>");
  process.exit(1);
}

const regions = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "regions.json"), "utf8")
);
const fc = JSON.parse(readFileSync(adminFile, "utf8"));

mkdirSync(join(outDir, "poly"), { recursive: true });
mkdirSync(join(outDir, "out"), { recursive: true });

const ringArea = (ring) => {
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  }
  return Math.abs(a / 2);
};
const featArea = (f) => {
  const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
  return polys.reduce((s, p) => s + ringArea(p[0]), 0);
};

const bboxOf = (geometry) => {
  let w = 180, s = 90, e = -180, n = -90;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]);
      s = Math.min(s, c[1]); n = Math.max(n, c[1]);
    } else c.forEach(walk);
  };
  walk(geometry.coordinates);
  const r = (x) => Math.round(x * 10000) / 10000;
  return [r(w), r(s), r(e), r(n)];
};

const meta = { built_at: new Date().toISOString(), regions: {} };
const extracts = [];
let failed = false;

for (const [key, def] of Object.entries(regions)) {
  if (key === "slovensko") {
    // celé Slovensko sa neextrahuje – použije sa vstupný PBF ako celok
    meta.regions[key] = { name: def.name, bbox: def.bbox };
    continue;
  }

  const candidates = fc.features.filter(
    (f) =>
      ["Polygon", "MultiPolygon"].includes(f.geometry?.type) &&
      String(f.properties?.admin_level) === String(def.admin_level) &&
      f.properties?.boundary === "administrative" &&
      (f.properties?.name === def.osm_name || f.properties?.["name:sk"] === def.osm_name)
  );

  if (candidates.length === 0) {
    console.error(`✗ Hranica „${def.osm_name}" (admin_level=${def.admin_level}) sa nenašla.`);
    failed = true;
    continue;
  }
  candidates.sort((a, b) => featArea(b) - featArea(a));
  const feature = candidates[0];

  const polyFile = join(outDir, "poly", `${key}.geojson`);
  writeFileSync(
    polyFile,
    JSON.stringify({ type: "Feature", properties: { name: def.osm_name }, geometry: feature.geometry })
  );

  meta.regions[key] = { name: def.name, bbox: bboxOf(feature.geometry) };
  extracts.push({
    output: `${key}.osm.pbf`,
    polygon: { file_name: resolve(polyFile), file_type: "geojson" }
  });
  console.log(`✓ ${def.name}  bbox=${meta.regions[key].bbox.join(",")}`);
}

if (failed) {
  console.error("Dostupné administratívne hranice v admin.geojson:");
  for (const f of fc.features) {
    if (["Polygon", "MultiPolygon"].includes(f.geometry?.type)) {
      console.error(`  admin_level=${f.properties?.admin_level}  ${f.properties?.name}`);
    }
  }
  process.exit(1);
}

writeFileSync(
  join(outDir, "config.json"),
  JSON.stringify({ directory: resolve(outDir, "out"), extracts }, null, 2)
);
writeFileSync(join(outDir, "meta.json"), JSON.stringify(meta, null, 2));
console.log(`Konfig: ${join(outDir, "config.json")} (${extracts.length} extraktov)`);
