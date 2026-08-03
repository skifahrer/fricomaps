#!/usr/bin/env node
/**
 * Vygeneruje statické MapLibre style.json súbory pre všetky témy.
 * Tie isté štýly použije web aj iOS aplikácia (MapLibre Native vie
 * načítať style.json priamo z URL GitHub Pages).
 *
 * Štýl sa naviaže na reálne dostupné assety:
 *   --sprite     … sprite index (JSON) – z neho sa vezme zoznam ikon, takže
 *                  nikdy neodkazujeme na ikonu, ktorá v sprite nie je;
 *                  ak je sprite SDF, štýl navyše nastaví farby ikon
 *   --fonts-dir  … adresár s glyfmi na Pages – z neho sa vyberú fontstacky
 *   --overrides  … úpravy z developer módu (poc/web/style-overrides.json)
 *
 * Použitie:
 *   node workers/build-styles.mjs --base-url=https://user.github.io/fricomaps \
 *        --region=slovensko --maxzoom=16 --out=_site/styles \
 *        --sprite=_site/sprites/osm-liberty.json --fonts-dir=_site/fonts \
 *        --overrides=poc/web/style-overrides.json
 */
import { mkdirSync, writeFileSync, readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  THEMES,
  buildStyle,
  normalizeOverrides,
  hasOverrides,
  paletteCoverage,
  MAX_TILE_Z,
  DEFAULT_DEM_TILES
} from "../poc/web/themes.js";

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.join("=")];
  })
);

const baseUrl = (args["base-url"] || "").replace(/\/$/, "");
const region = args.region || "slovensko";
const outDir = args.out || "_site/styles";
const maxzoom = Number(args.maxzoom || MAX_TILE_Z);
// Vrstevnice sú voliteľné – štýl ich zapne, len ak pipeline vyrobila .pmtiles.
const contoursMaxzoom = Number(args["contours-maxzoom"] || 14);
const hasContours = args.contours === "true" || args.contours === "1";
// Tieňovanie reliéfu sa dá vypnúť (--dem-tiles=none).
const demTiles =
  args["dem-tiles"] === "none" ? null : args["dem-tiles"] || DEFAULT_DEM_TILES;

if (!baseUrl) {
  console.error("Chýba --base-url (URL GitHub Pages stránky)");
  process.exit(1);
}

const regions = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "regions.json"), "utf8")
);
// Región nemusí byť v regions.json (custom región z osm.fr – Európa/svet);
// vtedy sa meno berie z --name, prípadne z kľúča regiónu.
const regionName = regions[region]?.name || args.name || region;

// ---------- ikony zo spritu ----------
// Zo sprite indexu sa berie nielen zoznam ikon, ale aj to, či je sprite SDF.
// SDF sprite (workers/build-sdf-sprite.mjs) je bez koliesok pod ikonami a dá
// sa mu nastaviť farba – štýl na to potom pridá `icon-color`.
let icons = [];
let sdfIcons = false;
if (args.sprite && existsSync(args.sprite)) {
  try {
    const index = JSON.parse(readFileSync(args.sprite, "utf8"));
    icons = Object.keys(index);
    sdfIcons = Object.values(index).some((e) => e && e.sdf);
    console.log(
      `Sprite: ${args.sprite} – ${icons.length} ikon${sdfIcons ? " (SDF, farbiteľné)" : ""}`
    );
  } catch (err) {
    console.warn(`⚠ Sprite ${args.sprite} sa nepodarilo prečítať: ${err.message}`);
  }
}
if (!icons.length) {
  console.warn("⚠ Sprite index nie je k dispozícii – použije sa záložný zoznam ikon.");
}

// ---------- úpravy z developer módu ----------
// Súbor je voliteľný; ak chýba alebo je prázdny, štýl je pôvodný.
const overridesPath =
  args.overrides || join(dirname(fileURLToPath(import.meta.url)), "..", "poc", "web", "style-overrides.json");
let overrides = null;
if (existsSync(overridesPath)) {
  try {
    const { overrides: clean, problems } = normalizeOverrides(
      JSON.parse(readFileSync(overridesPath, "utf8"))
    );
    for (const p of problems) console.warn(`⚠ ${overridesPath}: ${p}`);
    overrides = hasOverrides(clean) ? clean : null;
  } catch (err) {
    console.warn(`⚠ ${overridesPath} sa nepodarilo prečítať: ${err.message}`);
  }
}
console.log(
  overrides
    ? `Úpravy štýlu z developer módu: ${Object.keys(overrides.layers).length} vrstiev, ` +
        `${Object.values(overrides.palette).reduce((n, c) => n + Object.keys(c).length, 0)} farieb`
    : "Úpravy štýlu z developer módu: žiadne"
);

// ---------- fontstacky ----------
// Na Pages ležia glyfy v _site/fonts/<Fontstack>/<range>.pbf. Vyberieme
// reálne existujúce adresáre; ak žiadne nie sú, spadneme na verejnú službu.
const PREFERRED = {
  regular: ["Noto Sans Regular", "Open Sans Regular", "Roboto Regular"],
  bold: ["Noto Sans Bold", "Open Sans Bold", "Roboto Medium"],
  italic: ["Noto Sans Italic", "Open Sans Italic", "Noto Sans Regular"]
};

let availableStacks = [];
if (args["fonts-dir"] && existsSync(args["fonts-dir"])) {
  availableStacks = readdirSync(args["fonts-dir"]).filter(
    (d) =>
      statSync(join(args["fonts-dir"], d)).isDirectory() &&
      existsSync(join(args["fonts-dir"], d, "0-255.pbf"))
  );
}

const fonts = {};
for (const [role, candidates] of Object.entries(PREFERRED)) {
  fonts[role] =
    candidates.find((n) => availableStacks.includes(n)) ||
    availableStacks[0] ||
    candidates[0];
}

// Sprite, na ktorý sa odkazuje výsledný štýl (bez prípony).
const spriteUrl = (args["sprite-url"] || `${baseUrl}/sprites/osm-liberty`).replace(/\.json$/, "");

const glyphsUrl =
  args["glyphs-url"] ||
  (availableStacks.length
    ? `${baseUrl}/fonts/{fontstack}/{range}.pbf`
    : "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf");

if (availableStacks.length) {
  console.log(`Glyfy: lokálne (${availableStacks.length} fontstackov) → ${glyphsUrl}`);
} else {
  console.warn(`⚠ Lokálne glyfy nenájdené – používam ${glyphsUrl}`);
}
console.log(`Fonty: regular="${fonts.regular}" bold="${fonts.bold}" italic="${fonts.italic}"`);

// Každá farba témy musí byť v niektorej skupine palety, inak by sa v
// developer móde nedala nájsť ani zmeniť.
const coverage = paletteCoverage();
if (coverage.missing.length || coverage.extra.length) {
  console.error(
    `::error::PALETTE_GROUPS nesedia s témami – chýba: [${coverage.missing}], navyše: [${coverage.extra}]`
  );
  process.exit(1);
}

mkdirSync(outDir, { recursive: true });

for (const themeKey of Object.keys(THEMES)) {
  const style = buildStyle({
    theme: themeKey,
    tilesUrl: `pmtiles://${baseUrl}/tiles/${region}.pmtiles`,
    spriteUrl: spriteUrl,
    glyphsUrl,
    icons,
    fonts,
    maxzoom,
    sdfIcons,
    overrides,
    contoursUrl: hasContours
      ? `pmtiles://${baseUrl}/tiles/${region}-contours.pmtiles`
      : null,
    contoursMaxzoom,
    demTiles,
    name: `FricoMaps ${regionName} – ${THEMES[themeKey].label}`
  });
  const file = join(outDir, `${region}-${themeKey}.json`);
  writeFileSync(file, JSON.stringify(style, null, 2));
  console.log(`✓ ${file} (${style.layers.length} vrstiev)`);
}

console.log(
  `Vrstevnice: ${hasContours ? `áno (do z${contoursMaxzoom})` : "nie"}, ` +
    `tieňovanie reliéfu: ${demTiles ? "áno" : "nie"}`
);
