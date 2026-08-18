#!/usr/bin/env node
/**
 * Kontrola úprav z developer módu. Volá ju `Kontrola · lint workflowov`.
 *
 * DVE TICHÉ VECI, OBE ZAPLATENÉ:
 *
 * 1. **Nulová hrúbka čiary.** Políčko „hrúbka" v developer móde ukazuje pri
 *    krivke podľa zoomu prázdne „auto" a prázdne `input[type=number]` skočí
 *    šípkou dole na spodnú medzu. Kým tou medzou bola nula, jedno ťuknutie
 *    zhaslo celú vrstvu – mapa sa načítala, štýl bol platný, MapLibre nepovedal
 *    nič a v mape jednoducho neboli chodníky. Odvtedy je `line-width: 0` TVRDÁ
 *    chyba v `normalizeOverrides` a políčko má medzu 0,1; táto kontrola drží
 *    oboje (a zároveň to, že `text-halo-width: 0` chybou NIE JE – tam nula
 *    znamená „bez lemu", čo je bežná hodnota zo štýlu).
 *
 * 2. **Kopírovanie štýlu medzi vrstvami.** „Sprav túto vrstvu takú, ako je
 *    tamtá" odfotí `paint` hotového štýlu a zapíše ho ako úpravu. Keby z toho
 *    vypadlo čokoľvek, čo `normalizeOverrides` neprijme (krivka s viac než
 *    `MAX_PAINT_STOPS` zlomami, „bez výplne" na čiare, vlastnosť, ktorú cieľ
 *    nepozná), úprava by sa v prehliadači tvárila, že platí, a pipeline by ju
 *    pri zápise do repozitára potichu zahodila – v mape na Pages by potom bolo
 *    niečo iné než v prehliadači. Kontrola preto skúsi odfotiť KAŽDÚ vrstvu
 *    každej témy a typu mapy, vložiť ju do vrstvy toho istého aj iného druhu
 *    a trvá na tom, že `normalizeOverrides` nemá ani jednu výhradu a nič
 *    nezahodí.
 *
 * Použitie:
 *   node workers/lint/overrides.mjs
 */
import { THEMES, buildStyle, emptyOverrides, normalizeOverrides } from "../../poc/web/themes.js";
import { MAP_TYPE_IDS } from "../../poc/web/map-types.js";
import { snapshotStyle, pasteStyle, valueAtZoom } from "../../poc/web/layer-style.js";

let bad = 0;
const chyba = (subor, text) => {
  console.log(`::error file=${subor}::${text}`);
  bad += 1;
};

// ---------- 1. nulová hrúbka ----------
const width = (prop, value) =>
  normalizeOverrides({ layers: { x: { paint: { [prop]: value } } } });

for (const [prop, musiSpadnut] of [
  ["line-width", true],
  ["text-halo-width", false],
  ["icon-halo-width", false],
  ["circle-stroke-width", false]
]) {
  const { overrides, problems } = width(prop, 0);
  const prijate = overrides.layers.x?.paint?.[prop] === 0;
  if (musiSpadnut && (prijate || !problems.length)) {
    chyba(
      "poc/web/themes.js",
      `\`${prop}: 0\` prešlo cez normalizeOverrides. Čiara s nulovou hrúbkou ` +
      `sa nekreslí a v mape to vyzerá ako chýbajúce dáta – vrstva sa má vypínať ` +
      `cez \`visible\`, nie hrúbkou.`
    );
  }
  if (!musiSpadnut && !prijate) {
    chyba(
      "poc/web/themes.js",
      `\`${prop}: 0\` normalizeOverrides odmietol, hoci nula tam znamená ` +
      `„bez lemu" – to je bežná hodnota zo štýlu, nie chyba.`
    );
  }
}

// Kladná hrúbka musí prejsť ďalej – kontrola vyššie sa nesmie zvrhnúť na
// „zakážme hrúbku".
if (width("line-width", 1.5).overrides.layers.x?.paint?.["line-width"] !== 1.5) {
  chyba("poc/web/themes.js", "`line-width: 1.5` sa cez normalizeOverrides nedostalo.");
}

// ---------- 2. kopírovanie štýlu ----------
const styles = [];
for (const theme of Object.keys(THEMES)) {
  for (const mapType of MAP_TYPE_IDS) {
    styles.push({
      kde: `${theme} × ${mapType}`,
      style: buildStyle({
        theme,
        mapType,
        tilesUrl: "pmtiles://x/t.pmtiles",
        spriteUrl: "https://x/sprite",
        glyphsUrl: "https://x/{fontstack}/{range}.pbf",
        contoursUrl: "pmtiles://x/c.pmtiles",
        rocksUrl: "pmtiles://x/r.pmtiles",
        trailsUrl: "pmtiles://x/tr.pmtiles",
        featuresUrl: "pmtiles://x/f.pmtiles"
      })
    });
  }
}

let skusok = 0;
let odfotenych = 0;

/** Vloží odfotený štýl do vrstvy a overí, že to `normalizeOverrides` prijme. */
function skus(snap, target, kde) {
  const { patch } = pasteStyle(snap, target);
  if (!Object.keys(patch).length) return;
  skusok += 1;
  const raw = emptyOverrides();
  raw.layers[target.id] = patch;
  const { overrides, problems } = normalizeOverrides(raw);
  if (problems.length) {
    chyba(
      "poc/web/layer-style.js",
      `kopírovanie štýlu \`${snap.from}\` → \`${target.id}\` (${kde}) vyrobilo ` +
      `úpravu, ktorú normalizeOverrides odmieta: ${problems[0]}`
    );
    return;
  }
  const clean = overrides.layers[target.id] || {};
  for (const key of Object.keys(patch)) {
    if (key === "paint") continue;
    if (clean[key] === undefined) {
      chyba(
        "poc/web/layer-style.js",
        `kopírovanie štýlu \`${snap.from}\` → \`${target.id}\` (${kde}): ` +
        `\`${key}\` sa cez normalizeOverrides nedostalo – v prehliadači by ` +
        `platilo, v hotovej mape nie.`
      );
    }
  }
  for (const prop of Object.keys(patch.paint || {})) {
    if ((clean.paint || {})[prop] === undefined) {
      chyba(
        "poc/web/layer-style.js",
        `kopírovanie štýlu \`${snap.from}\` → \`${target.id}\` (${kde}): ` +
        `vlastnosť \`${prop}\` normalizeOverrides zahodil.`
      );
    }
  }
}

for (const { kde, style } of styles) {
  // Zástupca každého druhu vrstvy – do neho sa skúša vkladať naprieč druhmi.
  const zastupca = new Map();
  for (const layer of style.layers) if (!zastupca.has(layer.type)) zastupca.set(layer.type, layer);

  for (const layer of style.layers) {
    const snap = snapshotStyle(layer, {});
    odfotenych += 1;
    skus(snap, layer, kde);
    for (const target of zastupca.values()) if (target.id !== layer.id) skus(snap, target, kde);
  }
}

// ---------- 3. „čo to robí na tomto zoome" ----------
// Hodnota, ktorou sa napĺňa prázdne políčko, musí sedieť so štýlom aspoň
// v zlomoch – inak by šípka začínala inde, než mapa práve kreslí.
const krivka = ["interpolate", ["exponential", 1.5], ["zoom"], 11, 0.4, 16, 2.2];
for (const [z, cakane] of [[8, 0.4], [11, 0.4], [16, 2.2], [20, 2.2]]) {
  const dostal = valueAtZoom(krivka, z);
  if (dostal !== cakane) {
    chyba(
      "poc/web/layer-style.js",
      `valueAtZoom pri z${z} vrátilo ${dostal}, čakalo sa ${cakane}.`
    );
  }
}
if (valueAtZoom(["match", ["get", "x"], "a", 1, 2], 14) !== null) {
  chyba(
    "poc/web/layer-style.js",
    "valueAtZoom vrátilo číslo pre výraz podľa atribútu prvku – to sa jedným " +
    "zoomom povedať nedá a vymyslená hodnota je horšia než žiadna."
  );
}

console.log(
  `úpravy: ${bad} chýb (${odfotenych} odfotených vrstiev, ${skusok} vložení, ` +
  `${Object.keys(THEMES).length} tém × ${MAP_TYPE_IDS.length} typov mapy)`
);
process.exit(bad ? 1 : 0);
