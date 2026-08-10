#!/usr/bin/env node
/**
 * Kontroly hotového štýlu. Volá ich `Lint workflows`.
 *
 * DVE VECI, OBE TICHÉ:
 *   1. `fill` vrstva nad zmiešanou geometriou musí mať stráž (rozpis nižšie),
 *   2. odvodená vrstva (vzor, okraj) musí byť vidieť práve vtedy, keď je
 *      vidieť jej predloha.
 *
 * ČO STRÁŽI A PREČO. MapLibre `fill` vrstve NEPRESKOČÍ čiary. Prvok pustí do
 * výplne bez ohľadu na typ geometrie a otvorenú lomenú čiaru pošle earcutu,
 * ako keby to bol uzavretý prstenec – vypadne z toho sebaprekrývajúci sa
 * mnohouholník, ktorý s tou čiarou nemá nič spoločné.
 *
 * Presne to bola tá chyba, po ktorej táto kontrola vznikla: `pedestrian-area`
 * bola `fill` nad vrstvou `transportation` s `class in [pedestrian, path]`
 * a `minzoom: 13`, chodníky sú v tej vrstve ČIARY a Planetiler ich pri
 * `--transportation_z13_paths=true` púšťa do dlaždíc práve od z13. Na
 * obyčajnej mape (bez skál a vrstevníc) z toho boli od zoomu 13 „prerezané"
 * útvary, vo vnútri s farbou `pedestrian`, ktorá je od `background` na
 * nerozoznanie – čiže to vyzeralo, že tam je diera do podkladu.
 *
 * PREČO TO NEVIDEL NIKTO SKÔR: mapa sa načíta, štýl je platný, MapLibre
 * nepovie ani slovo. Je to tichý omyl v čistej podobe (pravidlo 8 v CLAUDE.md)
 * a jediné, čo ho spoľahlivo chytí, je toto: pozrieť sa na hotový štýl a
 * spýtať sa, či každá výplň nad zmiešanou vrstvou vie, že chce len plochy.
 *
 * Kontroluje sa VŠETKÝCH typ mapy × téma, nie jeden vygenerovaný súbor: profil
 * typu mapy vrstvy pridáva aj vypína, takže chyba môže byť len v jednom z nich.
 *
 * Použitie:
 *   node workers/lint/style.mjs
 */
import { THEMES, buildStyle } from "../../poc/web/themes.js";
import { MAP_TYPE_IDS, MAP_TYPES, applyMapType } from "../../poc/web/map-types.js";

/**
 * Vrstvy dlaždíc, v ktorých NIE JE len jeden typ geometrie – a čím to je.
 * Kým tu niečo je, každá `fill` nad tým musí mať v filtri `geometry-type`.
 */
const MIXED = {
  transportation:
    "cesty a chodníky sú čiary, ale pešia zóna, mólo a teleso mosta polygóny",
  aeroway: "dráhy a rolovacie dráhy sú čiary, odbavovacie plochy polygóny",
  park: "obrys je polygón, k nemu ide bod pre popisok (pointOnSurface)",
  piste: "workers/features/features.yml púšťa zjazdovku ako plochu AJ ako os (čiaru)",
  mountain_peak: "vrcholy sú body, ale `cliff`, `ridge` a `arete` čiary"
};

/** Výplňové typy vrstiev – tie, ktoré earcut naozaj triangulujú. */
const FILL = new Set(["fill", "fill-extrusion"]);

function styles() {
  const out = [];
  for (const theme of Object.keys(THEMES)) {
    for (const mapType of MAP_TYPE_IDS) {
      out.push({
        kde: `${mapType} / ${theme}`,
        style: buildStyle({
          theme,
          mapType,
          tilesUrl: "https://x/tiles.pmtiles",
          spriteUrl: "https://x/sprite",
          glyphsUrl: "https://x/fonts/{fontstack}/{range}.pbf",
          // Vrstvy z vlastných .pmtiles pridá štýl len vtedy, keď tie
          // archívy existujú – bez URL by sa `piste` nikdy neskontrolovala.
          contoursUrl: "https://x/contours.pmtiles",
          rocksUrl: "https://x/rocks.pmtiles",
          trailsUrl: "https://x/trails.pmtiles",
          featuresUrl: "https://x/features.pmtiles"
        })
      });
    }
  }
  return out;
}

let bad = 0;
let checked = 0;
let derived = 0;
const videne = new Set();

// ---------- 2. odvodená vrstva drží s predlohou ----------
// Vzor nad plochou je VLASTNÁ vrstva (`rock-area__pattern`) – MapLibre nevie
// kresliť výplň a vzor v jednej. Pravidlá typu mapy sa ale trafia podľa `id`,
// takže kým sa odvodená vrstva nepýtala za svoju predlohu, ostal vzor visieť
// nad vypnutou plochou: štýl platný, MapLibre ticho, v mape kresba bez toho,
// čo mala textúrovať.
//
// SKÚŠA SA TO NA VYROBENEJ DVOJICI, nie na tom, čo v štýle práve je. Dnešné
// pravidlo na skaly (`/^rock-/`) totiž odvodenú vrstvu chytí aj bez opravy –
// je to predpona. Kontrola postavená na dnešnom stave vrstiev by teda bola
// zelená aj nad pokazeným kódom a strážila by len samu seba. Preto sa berie
// prvé pravidlo, ktoré vypína vrstvy VYMENOVANÍM ID (tam predpona nepomôže),
// a overí sa, že vypne aj vrstvu od nich odvodenú.
for (const type of MAP_TYPES) {
  const rule = (type.rules || []).find(
    (r) => r.visible === false && Array.isArray(r.match?.id) && r.match.id.length
  );
  if (!rule) continue;
  const parentId = rule.match.id[0];
  const probe = {
    layers: [
      { id: parentId, type: "line", layout: {}, metadata: {} },
      {
        id: `${parentId}__pattern`,
        type: "fill",
        layout: {},
        metadata: { "frico:derived": parentId }
      }
    ]
  };
  applyMapType(probe, type.id);
  const hidden = (l) => (l.layout || {}).visibility === "none";
  derived += 1;
  if (hidden(probe.layers[0]) && !hidden(probe.layers[1])) {
    console.log(
      `::error file=poc/web/map-types.js::typ mapy \`${type.id}\` vypína ` +
      `\`${parentId}\`, ale vrstvu \`${parentId}__pattern\` odvodenú od nej ` +
      `nechal zapnutú. Vzor bez svojej plochy visí nad prázdnym podkladom ` +
      `a nikto to nepovie – \`matchesLayer\` sa musí na odvodenú vrstvu pýtať ` +
      `id jej predlohy (\`frico:derived\`).`
    );
    bad += 1;
  }
}

// A k tomu to isté nad hotovými štýlmi: odvodená vrstva musí mať predlohu
// a byť vidieť práve vtedy, keď je vidieť ona.
for (const { kde, style } of styles()) {
  const podla = new Map(style.layers.map((l) => [l.id, l]));
  for (const layer of style.layers) {
    const parentId = (layer.metadata || {})["frico:derived"];
    if (!parentId) continue;
    derived += 1;
    const parent = podla.get(parentId);
    const vis = (l) => ((l.layout || {}).visibility === "none" ? "vypnutá" : "zapnutá");
    if (!parent) {
      console.log(
        `::error file=poc/web/themes.js::odvodená vrstva \`${layer.id}\` ` +
        `(${kde}) sa odkazuje na predlohu \`${parentId}\`, ktorá v štýle nie je.`
      );
      bad += 1;
    } else if (vis(parent) !== vis(layer)) {
      console.log(
        `::error file=poc/web/map-types.js::odvodená vrstva \`${layer.id}\` ` +
        `je ${vis(layer)}, ale jej predloha \`${parentId}\` je ${vis(parent)} ` +
        `(${kde}).`
      );
      bad += 1;
    }
  }
}

// ---------- 1. výplň nad zmiešanou geometriou ----------
for (const { kde, style } of styles()) {
  for (const layer of style.layers) {
    const src = layer["source-layer"];
    if (!FILL.has(layer.type) || !MIXED[src]) continue;
    checked += 1;
    if (JSON.stringify(layer.filter ?? null).includes("geometry-type")) continue;
    // Tá istá vrstva vyjde v každej téme rovnako – hlás ju raz.
    if (videne.has(layer.id)) continue;
    videne.add(layer.id);
    console.log(
      `::error file=poc/web/themes.js::vrstva \`${layer.id}\` (${layer.type} ` +
      `nad \`${src}\`, ${kde}) nemá v filtri \`geometry-type\`. Vo vrstve ` +
      `\`${src}\` ${MIXED[src]}, a MapLibre pustí do výplne aj čiaru – ` +
      `earcutom z nej vyrobí nezmyselný mnohouholník, ktorý v mape vyzerá ` +
      `ako plocha prerezaná cez krajinu. Obaľ filter do \`polygonOnly(…)\`.`
    );
    bad += 1;
  }
}

console.log(
  `štýl: ${bad} chýb (${checked} výplní nad zmiešanou geometriou, ` +
  `${derived} skúšok odvodených vrstiev, ` +
  `${Object.keys(THEMES).length} tém × ${MAP_TYPE_IDS.length} typov mapy)`
);
process.exit(bad ? 1 : 0);
