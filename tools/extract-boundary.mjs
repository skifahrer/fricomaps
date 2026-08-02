#!/usr/bin/env node
/**
 * Z GeoJSON exportu administratívnych hraníc (osmium export) vyberie
 * polygón konkrétneho regiónu (podľa mena a admin_level z OSM) a uloží
 * ho ako samostatný GeoJSON pre `osmium extract --polygon`.
 * Zároveň vypíše bbox polygónu (west,south,east,north) na stdout.
 *
 * Použitie:
 *   node tools/extract-boundary.mjs <admin.geojson> <osm_name> <admin_level> <out.geojson>
 */
import { readFileSync, writeFileSync } from "node:fs";

const [inputFile, osmName, adminLevel, outFile] = process.argv.slice(2);
if (!outFile) {
  console.error("Použitie: extract-boundary.mjs <admin.geojson> <osm_name> <admin_level> <out.geojson>");
  process.exit(1);
}

const fc = JSON.parse(readFileSync(inputFile, "utf8"));

const candidates = fc.features.filter(
  (f) =>
    ["Polygon", "MultiPolygon"].includes(f.geometry?.type) &&
    String(f.properties?.admin_level) === String(adminLevel) &&
    f.properties?.boundary === "administrative" &&
    (f.properties?.name === osmName || f.properties?.["name:sk"] === osmName)
);

if (candidates.length === 0) {
  console.error(`Región „${osmName}" (admin_level=${adminLevel}) sa v ${inputFile} nenašiel.`);
  console.error("Dostupné hranice:");
  for (const f of fc.features) {
    if (["Polygon", "MultiPolygon"].includes(f.geometry?.type)) {
      console.error(`  admin_level=${f.properties?.admin_level}  ${f.properties?.name}`);
    }
  }
  process.exit(1);
}

// Pri viacerých kandidátoch (napr. duplicitné relácie) vezmi najväčší polygón.
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
candidates.sort((a, b) => featArea(b) - featArea(a));
const feature = candidates[0];

writeFileSync(
  outFile,
  JSON.stringify({ type: "Feature", properties: { name: osmName }, geometry: feature.geometry })
);

// bbox
let w = 180, s = 90, e = -180, n = -90;
const walk = (coords) => {
  if (typeof coords[0] === "number") {
    w = Math.min(w, coords[0]); e = Math.max(e, coords[0]);
    s = Math.min(s, coords[1]); n = Math.max(n, coords[1]);
  } else {
    coords.forEach(walk);
  }
};
walk(feature.geometry.coordinates);

const round = (x) => Math.round(x * 10000) / 10000;
console.log([round(w), round(s), round(e), round(n)].join(","));
