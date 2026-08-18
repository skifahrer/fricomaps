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
 * NEROZŤAHUJE SA (žiadne `stretchX`/`stretchY`), a je to opravená chyba.
 *
 * Kedysi tu stálo, že „rozťahovanie SDF nekazí, lebo v naťahovanom pásme je
 * vzdialenostné pole rovnobežné s hranou". Tá úvaha platí pre HRANY, ale nie
 * pre celok, a v mape z toho bol ROZMAZANÝ KRÍŽ namiesto štítka: SDF nesie
 * vzdialenosť V PIXELOCH, takže natiahnutím pásma sa pole rozladí voči novej
 * geometrii (gradient sa zriedi a prah 0,75 rozmaže), a na švíkoch medzi
 * naťahovaným pásmom a pevným rohom na seba hodnoty nenadviažu – obrys sa
 * pretrhne a rohy odpadnú. Deväťdielne naťahovanie je robené na BEŽNÝ raster,
 * nie na vzdialenostné pole.
 *
 * Namerané (MapLibre 4.7.1, `icon-text-fit: both`, text-size 11, „D1"):
 *   so `stretchX`/`stretchY`  … 89 × 85 px, rozmazaný kríž
 *   bez nich                  … ~20 × 14 px, ostrý zaoblený obdĺžnik
 *
 * CENA: `icon-text-fit` teraz škáluje obrázok CELÝ, takže sa s dĺžkou čísla
 * škáluje aj polomer zaoblenia – z „D1" je zaoblený obdĺžnik, z „III/3059"
 * kapsula. Je to viditeľne horšie než pravý obdĺžnik, ale nesúmerne lepšie
 * než kríž, ktorý tam bol. Pravý obdĺžnik pri každej dĺžke by chcel obrázok
 * BEZ SDF (deväťdielne naťahovanie na ňom funguje, ako má) a s farbou
 * zapečenou pri builde – čo je iná pipeline a stojí za samostatné rozhodnutie,
 * lebo farba štítka sa tým prestane dať ladiť v developer móde.
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
// Polomer je ODMERANÝ Z NAOZAJSTNEJ ZNAČKY, nie odhadnutý: na úradnom
// slovenskom štítku D1/R1 (Wikimedia Commons `D1-SVK-2020.svg`) má zaoblenie
// 8 % výšky červeného poľa. Dovtedy tu boli 4 px na 18 px poli, teda 22 % –
// takmer trojnásobok, a štítok preto pôsobil ako pilulka, nie ako dopravná
// značka. Pri dlhom čísle to bolo ešte vypuklejšie: `icon-text-fit` škáluje
// obrázok v oboch osiach zvlášť, takže sa vodorovný polomer natiahol s ním
// a z „III/3059" bola kapsula. S malým polomerom to ostane obdĺžnik.
//
// 8 % z 18 px = 1,44 px; 1,5 je najbližšie, čo má na mriežke zmysel.
export const SHIELD_SHAPES = [
  { id: "shield", label: "Štítok – zaoblený obdĺžnik (ako značka D1/R1)", radius: 1.5 },
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

  // BEZ `stretchX`/`stretchY`/`content` – viď rozpis v hlavičke súboru.
  // `icon-text-fit` tým obrázok škáluje celý a rovnomerne, čo je na SDF
  // jediný správny spôsob: vzdialenostné pole sa škáluje s ním a ostane
  // konzistentné. Pridať sem pásma späť znamená vrátiť ten kríž – stráži
  // to `workers/lint/shields.mjs`.
  return { width: size, height: size, data };
}
