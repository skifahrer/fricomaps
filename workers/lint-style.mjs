#!/usr/bin/env node
/**
 * Kontrola štýlu: `fill` vrstva nad zmiešanou geometriou musí mať stráž.
 * Volá ju `Lint workflows`.
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
 *   node workers/lint-style.mjs
 */
import { THEMES, buildStyle } from "../poc/web/themes.js";
import { MAP_TYPE_IDS } from "../poc/web/map-types.js";

/**
 * Vrstvy dlaždíc, v ktorých NIE JE len jeden typ geometrie – a čím to je.
 * Kým tu niečo je, každá `fill` nad tým musí mať v filtri `geometry-type`.
 */
const MIXED = {
  transportation:
    "cesty a chodníky sú čiary, ale pešia zóna, mólo a teleso mosta polygóny",
  aeroway: "dráhy a rolovacie dráhy sú čiary, odbavovacie plochy polygóny",
  park: "obrys je polygón, k nemu ide bod pre popisok (pointOnSurface)",
  piste: "workers/features.yml púšťa zjazdovku ako plochu AJ ako os (čiaru)",
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
const videne = new Set();

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
  `${Object.keys(THEMES).length} tém × ${MAP_TYPE_IDS.length} typov mapy)`
);
process.exit(bad ? 1 : 0);
