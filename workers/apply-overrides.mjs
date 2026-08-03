#!/usr/bin/env node
/**
 * Prevezme úpravy štýlu z developer módu a uloží ich do zdrojáku
 * (`poc/web/style-overrides.json`), odkiaľ ich potom použije web aj
 * generátor statických štýlov pre iOS.
 *
 * Vstup je JSON stiahnutý z developer módu – buď zo súboru, zo štandardného
 * vstupu, alebo priamo ako text z inputu workflowu. Pred zápisom sa
 * skontroluje a prečistí tou istou funkciou (`normalizeOverrides`), akú
 * používa prehliadač, takže do repa sa nedostane neznáma farba, neplatný hex
 * ani vlastnosť, ktorú štýl nevie prepísať.
 *
 * Použitie:
 *   node workers/apply-overrides.mjs --file=overrides.json
 *   node workers/apply-overrides.mjs --stdin < overrides.json
 *   node workers/apply-overrides.mjs --file=x.json --check   (len kontrola)
 *   node workers/apply-overrides.mjs --reset                 (vráti pôvodný štýl)
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  normalizeOverrides,
  emptyOverrides,
  hasOverrides,
  THEMES,
  PALETTE_LABELS
} from "../poc/web/themes.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const TARGET = join(root, "poc", "web", "style-overrides.json");

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.join("=") || "true"];
  })
);

function readInput() {
  if (args.reset) return emptyOverrides();
  if (args.file) return JSON.parse(readFileSync(args.file, "utf8"));
  if (args.stdin) return JSON.parse(readFileSync(0, "utf8"));
  console.error(
    "Chýba vstup: --file=<json>, --stdin alebo --reset (pozri hlavičku súboru)"
  );
  process.exit(2);
}

let raw;
try {
  raw = readInput();
} catch (err) {
  console.error(`::error::Vstup sa nepodarilo prečítať ako JSON: ${err.message}`);
  process.exit(1);
}

const { overrides, problems } = normalizeOverrides(raw);

for (const p of problems) console.log(`::warning::${p}`);

// ---------- prehľad, čo sa vlastne mení ----------
const summary = [];
for (const [theme, colors] of Object.entries(overrides.palette)) {
  const names = Object.keys(colors)
    .map((k) => PALETTE_LABELS[k] || k)
    .join(", ");
  summary.push(`  téma ${THEMES[theme].label}: ${Object.keys(colors).length} farieb (${names})`);
}
const hidden = Object.entries(overrides.layers).filter(([, o]) => o.visible === false);
const recolored = Object.entries(overrides.layers).filter(([, o]) => o.paint);
const rezoomed = Object.entries(overrides.layers).filter(
  ([, o]) => o.minzoom != null || o.maxzoom != null
);
const patterned = Object.entries(overrides.layers).filter(([, o]) => o.pattern);
const outlined = Object.entries(overrides.layers).filter(([, o]) => o.outline);
const dashed = Object.entries(overrides.layers).filter(([, o]) => o.dash);
if (hidden.length) summary.push(`  skryté vrstvy: ${hidden.map(([id]) => id).join(", ")}`);
if (recolored.length) summary.push(`  prefarbené vrstvy: ${recolored.length}`);
if (rezoomed.length) summary.push(`  zmenený rozsah zoomu: ${rezoomed.length}`);
if (patterned.length) {
  summary.push(
    `  vzory: ${patterned.map(([id, o]) => `${id} → ${o.pattern.id}`).join(", ")}`
  );
}
if (outlined.length) summary.push(`  okraje: ${outlined.map(([id]) => id).join(", ")}`);
if (dashed.length) {
  summary.push(`  prerušenie čiar: ${dashed.map(([id, o]) => `${id} → ${o.dash}`).join(", ")}`);
}
if (overrides.poi.hidden.length) {
  summary.push(`  skryté POI triedy: ${overrides.poi.hidden.join(", ")}`);
}

console.log(
  hasOverrides(overrides)
    ? `Úpravy štýlu:\n${summary.join("\n")}`
    : "Žiadne úpravy – štýl zostane pôvodný."
);

if (args.check) {
  console.log("Kontrola prebehla, súbor sa nezapisuje (--check).");
  process.exit(0);
}

const payload = {
  version: 1,
  updated_at: new Date().toISOString(),
  palette: overrides.palette,
  layers: overrides.layers,
  poi: overrides.poi
};
writeFileSync(TARGET, `${JSON.stringify(payload, null, 2)}\n`);
console.log(`✓ zapísané do ${TARGET}`);
