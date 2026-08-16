#!/usr/bin/env node
/**
 * Značené trasy: čo o nich vie štýl, musí sedieť s tým, čo do dlaždíc píšu
 * dáta – a s tým, čo panel v developer móde ukazuje.
 *
 * PREČO. Pásik trasy je jediná vec v mape, ktorá sa skladá z TROCH miest
 * naraz: `workers/trails/routes.py` rozhodne, na ktorú stranu cesty trasa
 * patrí a koľká je v rade, `workers/trails/trails.yml` to musí pustiť do
 * dlaždíc a `poc/web/themes.js` to prepočíta na `line-offset`. Keď sa
 * ktorékoľvek dve z nich rozídu, nič nespadne: mapa sa vykreslí, len sú
 * cyklotrasy na tej istej strane ako turistické, alebo sú pásiky odlepené od
 * cesty na polovicu obrazovky. To je tichý omyl (pravidlo 8 v CLAUDE.md).
 *
 * ČO SA KONTROLUJE:
 *
 *   1. STRANA CESTY je v `SIDE_BY_ROUTE` (routes.py) aj v `TRAIL_TYPES.side`
 *      (themes.js). Dáta podľa nej číslujú rady, developer mode podľa nej
 *      píše „vľavo / vpravo od cesty" – rozídené znamená panel, ktorý klame.
 *   2. KRIVKY ODSTUPU majú rovnaké zlomy ako `TRAIL_OFFSET_ZOOMS`. Skladajú sa
 *      do jedného `interpolate` PO INDEXOCH (`["zoom"]` smie byť len vstupom
 *      toho najvrchnejšieho), takže posunutý zlom v jednej z nich by ticho
 *      spároval odstup zo z14 s rozostupom zo z16.
 *   3. PREDVOLENÉ ODSTUPY (`TRAIL_GAP_DEFAULTS`) sú naozaj hodnoty pri
 *      `TRAIL_GAP_ZOOM` – z nich sa škáluje celá krivka, takže nesúlad by
 *      posunul pásiky aj bez jedinej úpravy.
 *   4. VZOR ČIARY každého druhu je platná predvoľba z `patterns.js` – vzor,
 *      ktorý neexistuje, sa ticho zmení na plnú čiaru.
 *   5. `side`, `off` a `way` sú v schéme dlaždíc (`trails.yml`). Bez nich by
 *      výraz čítal prázdno a všetky pásiky by si sadli na jednu kopu.
 *   6. SMER ČIAR sa naozaj reťazí. `orient_ways` sa musí zavolať a jeho
 *      výsledok podať do `Ways` – keby sa niekde po ceste stratil, cesty by
 *      sa kreslili v poradí, v akom ich nakreslil ten, kto ich zadával, a
 *      pásik by preskakoval z jednej strany chodníka na druhú. Nespadne to,
 *      len to vyzerá zle na mape.
 *   7. ROZOSTUP JE ŠÍRKA PÁSIKA – tá istá krivka aj ten istý druh
 *      interpolácie. Kým to boli dve krivky (jedna `linear`, druhá
 *      `exponential 1.5`), sedeli si len v šiestich zlomoch a medzi nimi sa
 *      rozchádzali: pri z18 bola medzi trasami medzera 0,65 px, presvital cez
 *      ňu ich podklad a vyzeralo to, že sú trasy od seba odsunuté.
 *   8. ODSTUP OD CESTY NEPRESIAHNE `TRAIL_OFFSET_LIMIT_M` METROV. Pixel je pri
 *      z13 dvanásť metrov, takže „2,6 px od chodníka" znamená 33 m – viac než
 *      je v horách rozostup ramien serpentíny. `line-offset` potom obieha
 *      vlásenku oblúkom širším než je zákruta a v mape je z pásika farebná
 *      plocha. Toto je to číslo, ktoré sa nesmie ticho vrátiť späť.
 *   9. OSTRÉ ZLOMY SA ZAOBĽUJÚ (`ease_corners` v routes.py a naozaj sa aj
 *      volá). Posun vrcholu pri `line-offset` je odstup delený kosínusom
 *      polovičného uhla, čiže pri ostrom zlome rastie nad všetky medze –
 *      z pásika vypadne výbežok dlhý desiatky pixelov.
 *
 * Spustenie (aj lokálne):
 *   node workers/lint/trails.mjs
 */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  TRAIL_TYPES,
  TRAIL_OFFSET_ZOOMS,
  TRAIL_OFFSET_ROAD,
  TRAIL_OFFSET_PATH,
  TRAIL_PITCH,
  TRAIL_STRIPE,
  TRAIL_OFFSET_LIMIT_M,
  METRES_PER_PX_Z0,
  TRAIL_GAP_DEFAULTS,
  TRAIL_GAP_ZOOM,
  THEMES,
  buildStyle
} from "../../poc/web/themes.js";
import { DASH_IDS } from "../../poc/web/patterns.js";

// Vlastný priečinok je `workers/lint`, koreň repozitára o dve úrovne vyššie.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ROUTES = join(ROOT, "workers", "trails", "routes.py");
const SCHEMA = join(ROOT, "workers", "trails", "trails.yml");

const bad = [];

// ---- 1. strana cesty ----
const py = readFileSync(ROUTES, "utf8");
const sideBlock = py.match(/SIDE_BY_ROUTE\s*=\s*\{([^}]*)\}/);
if (!sideBlock) {
  bad.push(
    "workers/trails/routes.py nemá SIDE_BY_ROUTE – bez nej sa nedá povedať, " +
      "na ktorú stranu cesty ktorá trasa patrí."
  );
} else {
  const sides = {};
  for (const m of sideBlock[1].matchAll(/"([a-z_]+)"\s*:\s*(-?\d+)/g)) {
    sides[m[1]] = Number(m[2]);
  }
  for (const type of TRAIL_TYPES) {
    if (sides[type.id] === undefined) {
      bad.push(
        `Druh trasy "${type.id}" je v TRAIL_TYPES, ale nie v SIDE_BY_ROUTE ` +
          "(workers/trails/routes.py) – dáta mu stranu nepridelia."
      );
    } else if (sides[type.id] !== type.side) {
      bad.push(
        `Strana trasy "${type.id}" sa rozišla: dáta ${sides[type.id]}, ` +
          `štýl ${type.side}. Developer mode podľa štýlu píše, na ktorej ` +
          "strane cesty trasu nájdeš – takto by klamal."
      );
    }
  }
  for (const id of Object.keys(sides)) {
    if (!TRAIL_TYPES.some((t) => t.id === id)) {
      bad.push(`SIDE_BY_ROUTE pozná druh "${id}", ktorý TRAIL_TYPES nemá.`);
    }
  }
}

// ---- 2. zlomy kriviek ----
const curves = [
  ["TRAIL_OFFSET_ROAD", TRAIL_OFFSET_ROAD],
  ["TRAIL_OFFSET_PATH", TRAIL_OFFSET_PATH],
  ["TRAIL_PITCH", TRAIL_PITCH]
];
for (const [name, stops] of curves) {
  const zooms = stops.map(([z]) => z);
  if (zooms.length !== TRAIL_OFFSET_ZOOMS.length ||
      zooms.some((z, i) => z !== TRAIL_OFFSET_ZOOMS[i])) {
    bad.push(
      `${name} má zlomy [${zooms}], ale TRAIL_OFFSET_ZOOMS hovorí ` +
        `[${TRAIL_OFFSET_ZOOMS}]. Krivky sa skladajú po indexoch, takže by ` +
        "sa odstup z jedného zoomu spároval s rozostupom z iného."
    );
  }
}

// ---- 3. predvolené odstupy ----
const refs = {
  road: TRAIL_OFFSET_ROAD,
  path: TRAIL_OFFSET_PATH,
  pitch: TRAIL_PITCH
};
for (const [key, stops] of Object.entries(refs)) {
  const at = (stops.find(([z]) => z === TRAIL_GAP_ZOOM) || [])[1];
  if (at !== TRAIL_GAP_DEFAULTS[key]) {
    bad.push(
      `TRAIL_GAP_DEFAULTS.${key} je ${TRAIL_GAP_DEFAULTS[key]}, ale krivka má ` +
        `pri z${TRAIL_GAP_ZOOM} hodnotu ${at}. Z toho pomeru sa škáluje celá ` +
        "krivka – pásiky by sa posunuli aj bez jedinej úpravy."
    );
  }
}

// ---- 4. vzory čiar ----
for (const type of TRAIL_TYPES) {
  if (!DASH_IDS.includes(type.dash)) {
    bad.push(
      `Druh trasy "${type.id}" má vzor čiary "${type.dash}", ktorý ` +
        "patterns.js nepozná – v mape by z neho bola plná čiara."
    );
  }
}

// ---- 6. smer čiar sa reťazí a ten výsledok sa aj použije ----
if (!/def orient_ways\(/.test(py)) {
  bad.push(
    "workers/trails/routes.py nemá `orient_ways` – smer čiar by sa potom bral " +
      "z tvaru jednej čiary a pásik by pri severojužnom chodníku preskakoval " +
      "na druhú stranu na každom druhom úseku."
  );
} else if (!/Ways\(\s*routes\.by_way\s*,\s*fh\s*,\s*flipped\s*\)/.test(py)) {
  bad.push(
    "workers/trails/routes.py nepodáva výsledok `orient_ways` do `Ways(...)` – " +
      "smery sa spočítajú a zahodia, takže pásiky budú preskakovať tak ako " +
      "predtým a nič to nepovie."
  );
}

// ---- 5. atribúty v schéme dlaždíc ----
const yml = readFileSync(SCHEMA, "utf8");
for (const key of ["side", "off", "way"]) {
  if (!new RegExp(`^\\s*-\\s*key:\\s*${key}\\s*$`, "m").test(yml)) {
    bad.push(
      `workers/trails/trails.yml nepúšťa do dlaždíc atribút \`${key}\`, ` +
        "hoci ho výraz `line-offset` v štýle číta. Bez neho si všetky pásiky " +
        "sadnú na jednu kopu vedľa cesty."
    );
  }
}

// ---- 7. rozostup dvoch trás JE šírka pásika ----
// Nie „rovnaké čísla v zlomoch", ale tá istá krivka aj ten istý druh
// interpolácie – medzi zlomami sa `linear` a `exponential 1.5` rozchádzajú.
const sameStops = (a, b) =>
  a.length === b.length && a.every(([z, v], i) => z === b[i][0] && v === b[i][1]);
if (!sameStops(TRAIL_PITCH, TRAIL_STRIPE)) {
  bad.push(
    "TRAIL_PITCH nie je TRAIL_STRIPE – rozostup dvoch trás musí byť šírka " +
      "pásika, inak medzi nimi presvitá ich podklad a vyzerá to, že sú trasy " +
      "od seba odsunuté."
  );
}
const trailStyle = buildStyle({
  theme: Object.keys(THEMES)[0],
  tilesUrl: "https://x/tiles.pmtiles",
  spriteUrl: "https://x/sprite",
  glyphsUrl: "https://x/fonts/{fontstack}/{range}.pbf",
  trailsUrl: "https://x/trails.pmtiles"
});
const stripeLayer = trailStyle.layers.find((l) => l.id === "trail-hiking");
if (!stripeLayer) {
  bad.push("V štýle nie je vrstva `trail-hiking` – pásik trasy sa nekreslí.");
} else {
  const width = stripeLayer.paint["line-width"];
  const offset = stripeLayer.paint["line-offset"];
  const widthStops = width.slice(3).filter((_, i) => i % 2 === 0);
  const wanted = TRAIL_STRIPE.map(([z]) => z);
  if (widthStops.length !== wanted.length ||
      widthStops.some((z, i) => z !== wanted[i])) {
    bad.push(
      `Šírka pásika má zlomy [${widthStops}], ale rozostup [${wanted}]. ` +
        "Musí to byť tá istá krivka (TRAIL_STRIPE), inak sa medzi zlomami " +
        "rozídu a medzi trasami vznikne medzera."
    );
  }
  if (JSON.stringify(width[1]) !== JSON.stringify(offset[1])) {
    bad.push(
      `Šírka pásika sa interpoluje ${JSON.stringify(width[1])}, ale odstup ` +
        `${JSON.stringify(offset[1])}. Rozostup JE šírka pásika, takže musí ` +
        "rásť rovnako – inak sedia na seba len v zlomoch."
    );
  }
}

// ---- 8. odstup od cesty v METROCH ----
// Pixel je pri z13 dvanásť metrov. Odstup, ktorý je v pixeloch nenápadný, je
// v teréne širší než serpentína, po ktorej trasa ide – a `line-offset` z toho
// urobí plochu namiesto čiary.
for (const [key, stops] of [["road", TRAIL_OFFSET_ROAD], ["path", TRAIL_OFFSET_PATH]]) {
  const limit = TRAIL_OFFSET_LIMIT_M[key];
  for (const [z, px] of stops) {
    const metres = px * (METRES_PER_PX_Z0 / 2 ** z);
    if (metres > limit * 1.05) {
      bad.push(
        `Odstup pásika od ${key === "road" ? "cesty" : "chodníka"} je pri z${z} ` +
          `${px} px, čo je v teréne ${metres.toFixed(0)} m – limit je ` +
          `${limit} m (TRAIL_OFFSET_LIMIT_M). Toľko miesta pri chodníku ` +
          "v horách nie je: pásik obehne vlásenku oblúkom širším než zákruta " +
          "a v mape z neho bude farebná plocha."
      );
    }
  }
}

// ---- 9. ostré zlomy sa zaobľujú ----
if (!/def ease_corners\(/.test(py)) {
  bad.push(
    "workers/trails/routes.py nemá `ease_corners` – pri ostrom zlome je posun " +
      "vrcholu odstup delený kosínusom polovičného uhla, takže z pásika " +
      "vypadne výbežok dlhý desiatky pixelov."
  );
} else if (!/coords, eased = ease_corners\(coords\)/.test(py)) {
  bad.push(
    "workers/trails/routes.py `ease_corners` nepoužíva na geometriu ciest – " +
      "zaoblenie sa spočíta a zahodí, takže v serpentínach ostanú z pásikov " +
      "plochy a nič to nepovie."
  );
}

for (const m of bad) console.log(`::error::${m}`);
console.log(
  bad.length
    ? `Značené trasy: ${bad.length} chýb`
    : `Značené trasy: v poriadku ✓ (${TRAIL_TYPES.length} druhov, zlomy ` +
      `[${TRAIL_OFFSET_ZOOMS}], odstupy pri z${TRAIL_GAP_ZOOM} ` +
      `${JSON.stringify(TRAIL_GAP_DEFAULTS)})`
);
process.exit(bad.length ? 1 : 0);
