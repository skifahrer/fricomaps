/**
 * ŠTÍTOK ČÍSLA CESTY – „D1", „R1", „I/18".
 *
 * Číslo cesty je na mape iná vec než jej meno: meno beží pozdĺž cesty a číta
 * sa ako text, číslo je ZNAČKA – krátka, opakuje sa po celej dĺžke a musí byť
 * čitateľná aj cez les, tieňovanie a vrstevnice. Preto má podklad.
 *
 * TU JE LEN OBRÁZOK PODKLADU, nie to, ktorá cesta ho dostane; kto ho dostane
 * a akú farbu, rozhoduje `SHIELD_DEFS` v `themes.js` – tam sú aj triedy ciest
 * a paleta. Rozdelené je to takto preto, že tento súbor potrebuje pipeline
 * (`workers/assets/shields.mjs` z neho dopečie obrázky do spritu) a nemá čo
 * vedieť o triedach OSM.
 *
 * PREČO SDF A NIE HOTOVÝ FAREBNÝ OBRÁZOK. SDF obrázok sa v MapLibre dá
 * zafarbiť (`icon-color`) a orámovať (`icon-halo-color`, `icon-halo-width`),
 * takže na štyri témy × štyri triedy ciest stačí JEDEN obrázok namiesto
 * šestnástich – a farba štítka sa dá doladiť v developer móde ako každá iná,
 * bez prebuildovania spritu.
 *
 * PREČO ROZŤAHOVATEĽNÝ (`stretchX`/`stretchY`). Štítok má byť okolo textu,
 * ktorý má raz dva znaky („D1") a raz šesť („III/3059"). MapLibre to vie sám
 * (`icon-text-fit`), ale len pri obrázku, ktorý má povedané, KTORÁ jeho časť
 * sa smie natiahnuť: rohy nie, rovné časti hrán áno. Bez toho by sa
 * natiahol celý aj s rohmi a z obdĺžnika by bola pri dlhom čísle rozmazaná
 * kapsula.
 *
 * ROZŤAHOVANIE SDF NEKAZÍ: v pásme, ktoré sa naťahuje, je vzdialenostné pole
 * rovnobežné s hranou (mení sa len naprieč ňou), takže natiahnutie pozdĺž
 * hrany nemení nič. Rohy, kde sa pole mení v oboch smeroch, sa nenaťahujú
 * práve preto.
 */

/**
 * Miesto okolo štítka pre halo (= jeho orámovanie), v pixeloch pri
 * `pixelRatio` 1. Musí byť väčšie než najväčší rozumný `icon-halo-width`,
 * inak sa rámik oreže o okraj obrázka.
 */
export const SHIELD_PAD = 4;

/** Najmenší štítok (štvorec) bez toho okraja – od tejto veľkosti sa naťahuje. */
export const SHIELD_BOX = 18;

/** Dosah vzdialenostného poľa – rovnaká konvencia ako `workers/assets/sprite.mjs`. */
const SDF_RADIUS = 8;
const SDF_CUTOFF = 0.25;

/**
 * Tvary štítka. `radius` je polomer zaoblenia rohov v pixeloch pri
 * `pixelRatio` 1; `SHIELD_BOX / 2` je už úplný ovál.
 */
export const SHIELD_SHAPES = [
  { id: "shield", label: "Štítok – zaoblený obdĺžnik", radius: 4 },
  { id: "shield-round", label: "Štítok – oválny", radius: 8 }
];

export const SHIELD_SHAPE_IDS = SHIELD_SHAPES.map((s) => s.id);
export const DEFAULT_SHIELD_SHAPE = "shield";

/** Tvar podľa id; pri neznámom id vráti predvolený. */
export function shieldShape(id) {
  return SHIELD_SHAPES.find((s) => s.id === id) || SHIELD_SHAPES[0];
}

/**
 * Vzdialenosť bodu od zaobleného obdĺžnika (záporná vnútri).
 * Klasický vzorec cez „vzdialenosť od zmenšeného obdĺžnika mínus polomer".
 */
function roundedRectDistance(px, py, w, h, r) {
  const dx = Math.abs(px - w / 2) - (w / 2 - r);
  const dy = Math.abs(py - h / 2) - (h / 2 - r);
  const vx = Math.max(dx, 0);
  const vy = Math.max(dy, 0);
  return Math.sqrt(vx * vx + vy * vy) + Math.min(Math.max(dx, dy), 0) - r;
}

/**
 * Vykreslí štítok ako SDF obrázok pre sprite.
 *
 * @param {object} shape        položka zo `SHIELD_SHAPES`
 * @param {number} pixelRatio   1 alebo 2 (varianta @2x)
 * @returns {{width:number, height:number, data:Uint8Array,
 *            stretchX:number[][], stretchY:number[][], content:number[]}}
 *          `data` je RGBA (biela, SDF v alfe) – presne to, čo do atlasu
 *          zapisuje `workers/assets/sprite.mjs`.
 */
export function renderShield(shape, pixelRatio = 1) {
  const r = pixelRatio;
  const pad = SHIELD_PAD * r;
  const box = SHIELD_BOX * r;
  const radius = Math.min(shape.radius * r, box / 2);
  const size = box + 2 * pad;
  const data = new Uint8Array(size * size * 4);

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      // Stred pixela, nie jeho roh – inak je tvar o pol pixela posunutý.
      const dist = roundedRectDistance(x + 0.5 - pad, y + 0.5 - pad, box, box, radius);
      const alpha = Math.max(
        0,
        Math.min(255, Math.round(255 - 255 * (dist / (SDF_RADIUS * r) + SDF_CUTOFF)))
      );
      const i = (y * size + x) * 4;
      data[i] = 255;
      data[i + 1] = 255;
      data[i + 2] = 255;
      data[i + 3] = alpha;
    }
  }

  // Naťahuje sa len rovná časť hrán – rohy (polomer zaoblenia) nie. Pásmo
  // musí mať aspoň pixel, inak MapLibre nemá čo opakovať.
  const od = pad + radius;
  const doX = Math.max(od + 1, pad + box - radius);
  return {
    width: size,
    height: size,
    data,
    stretchX: [[od, doX]],
    stretchY: [[od, doX]],
    // Kam sa vojde text. Dva pixely od hrany na každej strane, aby sa
    // číslo nedotýkalo rámika; zvyšok dorovná `icon-text-fit-padding`.
    content: [pad + 2 * r, pad + 2 * r, pad + box - 2 * r, pad + box - 2 * r]
  };
}
