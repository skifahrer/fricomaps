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
 *   node workers/styles/overrides.mjs --file=overrides.json
 *   node workers/styles/overrides.mjs --stdin < overrides.json
 *   node workers/styles/overrides.mjs --file=x.json --check   (len kontrola)
 *   node workers/styles/overrides.mjs --reset                 (vráti pôvodný štýl)
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  normalizeOverrides,
  emptyOverrides,
  hasOverrides,
  THEMES,
  PALETTE_LABELS,
  DEFAULT_ICON_SOURCE,
  mapTypeDef
} from "../../poc/web/themes.js";

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
if (overrides.hillshade) summary.push("  tieňovanie reliéfu: zapnuté");
if (overrides.icons && overrides.icons !== DEFAULT_ICON_SOURCE) {
  summary.push(`  sada ikoniek: ${overrides.icons}`);
}
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
const reiconed = Object.entries(overrides.layers).filter(([, o]) => o.icon);
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
if (reiconed.length) {
  summary.push(`  ikony: ${reiconed.map(([id, o]) => `${id} → ${o.icon}`).join(", ")}`);
}
if (overrides.poi.hidden.length) {
  summary.push(`  skryté POI triedy: ${overrides.poi.hidden.join(", ")}`);
}

// Úpravy, ktoré platia len pre jeden typ mapy. Všetko vyššie je spoločné –
// tu je vidieť, čo si ktorá mapa robí po svojom.
for (const [typeId, m] of Object.entries(overrides.maps)) {
  const parts = [];
  const own = Object.entries(m.layers || {});
  const off = own.filter(([, o]) => o.visible === false).map(([id]) => id);
  const on = own.filter(([, o]) => o.visible === true).map(([id]) => id);
  const zoomed = own.filter(([, o]) => o.minzoom != null || o.maxzoom != null);
  const styled = own.filter(([, o]) => o.paint || o.dash || o.pattern || o.outline || o.icon);
  if (off.length) parts.push(`skryté: ${off.join(", ")}`);
  if (on.length) parts.push(`zapnuté navyše: ${on.join(", ")}`);
  if (zoomed.length) parts.push(`zoom: ${zoomed.length}`);
  if (styled.length) parts.push(`štýl: ${styled.length}`);
  if (m.poi?.hidden?.length) parts.push(`skryté POI: ${m.poi.hidden.join(", ")}`);
  if (parts.length) {
    summary.push(`  mapa ${mapTypeDef(typeId).label}: ${parts.join(" · ")}`);
  }
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
  version: 2,
  updated_at: new Date().toISOString(),
  icons: overrides.icons,
  hillshade: overrides.hillshade,
  palette: overrides.palette,
  layers: overrides.layers,
  poi: overrides.poi,
  // Úpravy pre jednotlivé typy máp (turistická, lyžiarska, cestná, …).
  maps: overrides.maps
};
writeFileSync(TARGET, `${JSON.stringify(payload, null, 2)}\n`);
console.log(`✓ zapísané do ${TARGET}`);
