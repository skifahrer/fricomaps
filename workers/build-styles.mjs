#!/usr/bin/env node
/**
 * Vygeneruje statické MapLibre style.json súbory pre všetky témy.
 * Tie isté štýly použije web aj iOS aplikácia (MapLibre Native vie
 * načítať style.json priamo z URL GitHub Pages).
 *
 * Štýl sa naviaže na reálne dostupné assety:
 *   --sprite     … sprite index (JSON) – z neho sa vezme zoznam ikon, takže
 *                  nikdy neodkazujeme na ikonu, ktorá v sprite nie je
 *   --fonts-dir  … adresár s glyfmi na Pages – z neho sa vyberú fontstacky
 *
 * Použitie:
 *   node workers/build-styles.mjs --base-url=https://user.github.io/fricomaps \
 *        --region=slovensko --maxzoom=16 --out=_site/styles \
 *        --sprite=_site/sprites/osm-liberty.json --fonts-dir=_site/fonts
 */
import { mkdirSync, writeFileSync, readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { THEMES, buildStyle, MAX_TILE_Z } from "../poc/web/themes.js";

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
let icons = [];
if (args.sprite && existsSync(args.sprite)) {
  try {
    icons = Object.keys(JSON.parse(readFileSync(args.sprite, "utf8")));
    console.log(`Sprite: ${args.sprite} – ${icons.length} ikon`);
  } catch (err) {
    console.warn(`⚠ Sprite ${args.sprite} sa nepodarilo prečítať: ${err.message}`);
  }
}
if (!icons.length) {
  console.warn("⚠ Sprite index nie je k dispozícii – použije sa záložný zoznam ikon.");
}

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

mkdirSync(outDir, { recursive: true });

for (const themeKey of Object.keys(THEMES)) {
  const style = buildStyle({
    theme: themeKey,
    tilesUrl: `pmtiles://${baseUrl}/tiles/${region}.pmtiles`,
    spriteUrl: `${baseUrl}/sprites/osm-liberty`,
    glyphsUrl,
    icons,
    fonts,
    maxzoom,
    name: `FricoMaps ${regionName} – ${THEMES[themeKey].label}`
  });
  const file = join(outDir, `${region}-${themeKey}.json`);
  writeFileSync(file, JSON.stringify(style, null, 2));
  console.log(`✓ ${file} (${style.layers.length} vrstiev)`);
}
