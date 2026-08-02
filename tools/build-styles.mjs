#!/usr/bin/env node
/**
 * Vygeneruje statické MapLibre style.json súbory pre všetky témy.
 * Tie isté štýly použije web aj iOS aplikácia (MapLibre Native vie
 * načítať style.json priamo z URL GitHub Pages).
 *
 * Použitie:
 *   node tools/build-styles.mjs --base-url=https://user.github.io/fricomaps \
 *        --region=slovensko --out=_site/styles
 */
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { THEMES, buildStyle } from "../web/themes.js";

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.join("=")];
  })
);

const baseUrl = (args["base-url"] || "").replace(/\/$/, "");
const region = args.region || "slovensko";
const outDir = args.out || "_site/styles";

if (!baseUrl) {
  console.error("Chýba --base-url (URL GitHub Pages stránky)");
  process.exit(1);
}

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const regions = JSON.parse(
  readFileSync(join(repoRoot, "pipeline", "regions.json"), "utf8")
);
if (!regions[region]) {
  console.error(`Neznámy región: ${region}`);
  process.exit(1);
}

mkdirSync(outDir, { recursive: true });

for (const themeKey of Object.keys(THEMES)) {
  const style = buildStyle({
    theme: themeKey,
    tilesUrl: `pmtiles://${baseUrl}/tiles/${region}.pmtiles`,
    spriteUrl: `${baseUrl}/sprites/osm-liberty`,
    glyphsUrl: "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf",
    name: `FricoMaps ${regions[region].name} – ${THEMES[themeKey].label}`
  });
  const file = join(outDir, `${region}-${themeKey}.json`);
  writeFileSync(file, JSON.stringify(style, null, 2));
  console.log(`✓ ${file}`);
}
