/**
 * Čítanie a prenášanie ŠTÝLU JEDNEJ VRSTVY – bez DOM, aby sa to dalo spustiť
 * aj z kontroly (`workers/lint/overrides.mjs`).
 *
 * Dve otázky, ktoré developer mode potreboval a nemal:
 *
 *   1. **„Čo tá vlastnosť práve teraz robí?"** V štýle je hrúbka čiary
 *      spravidla `interpolate` podľa zoomu, takže políčko ukazuje „auto"
 *      a človek nemá z čoho vyjsť. `valueAtZoom` z krivky vytiahne hodnotu
 *      na tom zoome, na ktorom mapa práve stojí.
 *   2. **„Sprav túto vrstvu takú, ako je tamtá."** `snapshotStyle` odfotí
 *      ÚČINNÝ vzhľad vrstvy (to, čo je naozaj v štýle, nie len úpravu nad ním)
 *      a `pasteStyle` ho preloží na inú vrstvu.
 *
 * PREČO ÚČINNÝ VZHĽAD A NIE ÚPRAVA. Keby sa kopírovala len úprava, kopírovanie
 * z vrstvy, ktorú nikto neladil, by vrátilo prázdno – a pritom práve to je
 * najčastejšie zadanie („nech sú múry ako ploty"). Odfotí sa preto `paint`
 * hotového štýlu; krivka podľa zoomu sa uloží ako zoomové zlomy, ktoré formát
 * úprav pozná.
 *
 * PREKLAD MEDZI DRUHMI VRSTIEV je podľa PRÍPONY vlastnosti, nie podľa mena:
 * `fill-color` na čiaru je `line-color`. Vlastnosť, ktorú cieľ nepozná
 * (`fill-outline-color` na čiare), sa NEVLOŽÍ a povie sa to – tichý polovičný
 * vklad by vyzeral ako pokazené kopírovanie.
 */
import {
  MAX_PAINT_STOPS,
  MAX_DISPLAY_Z,
  NO_FILL,
  sortStops,
  sortBands,
  isBandList
} from "./themes.js";
import { dashIdOf } from "./patterns.js";

export { dashIdOf };

/** Vlastnosti, ktoré formát úprav vôbec pozná (`cleanPaintScalar`). */
const SUFFIXES = ["-color", "-opacity", "-width", "-exaggeration"];

/**
 * Čo smie mať ktorý druh vrstvy. Zámerne je to VÝPOČET, nie „skús a uvidíš":
 * MapLibre neznámu `paint` vlastnosť odmietne aj s celým štýlom.
 */
const ALLOWED = {
  fill: ["fill-color", "fill-opacity", "fill-outline-color"],
  line: ["line-color", "line-opacity", "line-width"],
  "fill-extrusion": ["fill-extrusion-color", "fill-extrusion-opacity"],
  symbol: [
    "text-color", "text-opacity", "text-halo-color", "text-halo-width",
    "icon-color", "icon-opacity", "icon-halo-color", "icon-halo-width"
  ],
  circle: [
    "circle-color", "circle-opacity", "circle-stroke-color",
    "circle-stroke-width", "circle-stroke-opacity"
  ],
  background: ["background-color", "background-opacity"],
  // Tieňovanie reliéfu. `hillshade-exaggeration` je jeho sila – jediné
  // číslo, ktoré nie je krytie ani hrúbka a úpravy ho aj tak poznajú
  // (viď `cleanPaintScalar`).
  hillshade: [
    "hillshade-exaggeration",
    "hillshade-shadow-color",
    "hillshade-highlight-color",
    "hillshade-accent-color"
  ]
};

/** Predpona, pod ktorou má daný druh vrstvy svoju hlavnú vlastnosť. */
const PREFIX = {
  fill: "fill",
  line: "line",
  "fill-extrusion": "fill-extrusion",
  symbol: "text",
  circle: "circle",
  background: "background",
  hillshade: "hillshade"
};

/** Podporuje vrstva vzor a okraj? (plochy a čiary áno, popisky nie) */
export const canDecorate = (layer) =>
  layer?.type === "fill" || layer?.type === "line" || layer?.type === "fill-extrusion";

/** Je to hodnota, ktorú formát úprav unesie ako skalár? */
const isScalar = (v) =>
  typeof v === "number" || (typeof v === "string" && v.startsWith("#")) || v === NO_FILL;

/**
 * Hodnota vlastnosti na danom zoome.
 *
 * Pozná to, čo štýl naozaj vyrába: číslo, `interpolate` podľa zoomu (lineárny
 * aj exponenciálny), `step` podľa zoomu a oba tvary zo súboru úprav – krivku
 * `[[zoom, hodnota], …]` aj pásma `[[od, do, hodnota], …]`. Na čokoľvek iné
 * (výraz podľa atribútu prvku) vráti `null` – „to sa jedným číslom povedať
 * nedá" je poctivejšia odpoveď než vymyslený priemer.
 *
 * Farbu neinterpoluje: medzi dvoma zlomami vráti tú spodnú. Miešať hex
 * v sRGB by dalo inú farbu, než akú kreslí MapLibre (ten mieša inak), a tu
 * ide o to, S ČÍM ZAČAŤ, nie o presnú predlohu.
 */
export function valueAtZoom(value, zoom) {
  if (isScalar(value)) return value;
  if (!Array.isArray(value)) return null;

  // Zoomové PÁSMA z úprav: `[[od, do, hodnota], …]` – hodnota pásma, v ktorom
  // ten zoom leží; pod prvým a nad posledným krajné pásmo.
  if (isBandList(value)) return bandAt(sortBands(value), zoom);

  // Zoomové zlomy z úprav: `[[zoom, hodnota], …]`.
  if (Array.isArray(value[0])) {
    const stops = sortStops(value.filter((s) => Array.isArray(s) && s.length === 2));
    if (!stops.length) return null;
    return stopsAt(stops, zoom);
  }

  // `step` podľa zoomu – to, čo zo zoomových pásiem vyrobí `paintValue`.
  if (value[0] === "step") {
    const bands = stepToBands(value);
    return bands ? bandAt(bands, zoom) : null;
  }

  if (value[0] !== "interpolate") return null;
  const [, curve, input, ...rest] = value;
  // Interpolácia podľa niečoho iného než zoomu (napr. podľa atribútu) sa
  // jedným zoomom nezodpovie.
  if (!Array.isArray(input) || input[0] !== "zoom") return null;
  const stops = [];
  for (let i = 0; i + 1 < rest.length; i += 2) {
    if (typeof rest[i] !== "number" || !isScalar(rest[i + 1])) return null;
    stops.push([rest[i], rest[i + 1]]);
  }
  if (!stops.length) return null;
  const base = Array.isArray(curve) && curve[0] === "exponential" ? Number(curve[1]) || 1 : 1;
  return stopsAt(stops, zoom, base);
}

/** Hodnota pásma, v ktorom daný zoom leží (krajné pásma platia aj za okraj). */
function bandAt(bands, zoom) {
  if (!bands.length) return null;
  for (const [od, doZ, v] of bands) if (zoom >= od && zoom < doZ + 1) return v;
  return zoom < bands[0][0] ? bands[0][2] : bands[bands.length - 1][2];
}

/**
 * `["step", ["zoom"], v0, z1, v1, …]` → pásma `[[od, do, hodnota], …]`.
 *
 * Prvý výstup `step` platí od z0 (pod prvým zlomom nie je nič nižšie) a
 * posledný až po strop zobrazenia – pásma preto pokrývajú celý rozsah, tak
 * ako to `cleanPaintBands` vyžaduje. `null` = nie je to schodisko podľa zoomu.
 */
function stepToBands(value) {
  const [, input, base, ...rest] = value;
  if (!Array.isArray(input) || input[0] !== "zoom") return null;
  if (!isScalar(base)) return null;
  const hranice = [];
  for (let i = 0; i + 1 < rest.length; i += 2) {
    if (typeof rest[i] !== "number" || !isScalar(rest[i + 1])) return null;
    hranice.push([rest[i], rest[i + 1]]);
  }
  const bands = [];
  let od = 0;
  let v = base;
  for (const [z, next] of hranice) {
    if (z <= od) return null;
    bands.push([od, z - 1, v]);
    od = z;
    v = next;
  }
  bands.push([od, Math.max(od, MAX_DISPLAY_Z), v]);
  return bands;
}

/** Hodnota medzi zlomami; farby sa nemiešajú (vráti sa spodný zlom). */
function stopsAt(stops, zoom, base = 1) {
  if (zoom <= stops[0][0]) return stops[0][1];
  const last = stops[stops.length - 1];
  if (zoom >= last[0]) return last[1];
  for (let i = 0; i + 1 < stops.length; i += 1) {
    const [z0, v0] = stops[i];
    const [z1, v1] = stops[i + 1];
    if (zoom < z0 || zoom > z1) continue;
    if (typeof v0 !== "number" || typeof v1 !== "number") return v0;
    // Rovnaký vzorec, aký používa MapLibre pre `exponential` (a pre base 1
    // z neho vyjde lineárna interpolácia).
    const t =
      base === 1
        ? (zoom - z0) / (z1 - z0)
        : (base ** (zoom - z0) - 1) / (base ** (z1 - z0) - 1);
    return Math.round((v0 + (v1 - v0) * t) * 100) / 100;
  }
  return last[1];
}

/**
 * Hodnota z hotového štýlu → hodnota, akú unesie súbor úprav.
 * Krivka podľa zoomu sa zapíše ako zoomové zlomy a `step` ako zoomové pásma –
 * teda každá vec tým tvarom, ktorý ju vyrába. Keď ich je viac než strop
 * (`MAX_PAINT_STOPS`), nechajú sa krajné a rovnomerne rozložené vnútorné –
 * bez toho by `normalizeOverrides` celú vlastnosť zahodil.
 */
function snapValue(value) {
  if (isScalar(value)) return value;
  if (!Array.isArray(value)) return null;
  if (isBandList(value)) return thinBands(sortBands(value));
  if (Array.isArray(value[0])) {
    const stops = sortStops(value.filter((s) => Array.isArray(s) && s.length === 2));
    return stops.length ? thin(stops) : null;
  }
  // Schodisko podľa zoomu sa odfotí ako PÁSMA – teda ako to, čo ho vyrobilo.
  // Preložiť ho na krivku by z konštantných pásiem spravilo plynulý prechod
  // a vložená vrstva by vyzerala inak než tá, z ktorej sa kopírovalo.
  if (value[0] === "step") {
    const bands = stepToBands(value);
    return bands ? thinBands(bands) : null;
  }
  if (value[0] !== "interpolate") return null;
  const [, , input, ...rest] = value;
  if (!Array.isArray(input) || input[0] !== "zoom") return null;
  const stops = [];
  for (let i = 0; i + 1 < rest.length; i += 2) {
    if (typeof rest[i] !== "number" || !isScalar(rest[i + 1])) return null;
    stops.push([rest[i], rest[i + 1]]);
  }
  return stops.length ? thin(stops) : null;
}

/**
 * Preriedi PÁSMA na strop. Vypustené pásmo pohltí to pred ním (predĺži sa
 * jeho `do`), takže rad ostane SÚVISLÝ – medzeru by `cleanPaintBands`
 * odmietol a s ňou celú vlastnosť.
 */
function thinBands(bands) {
  if (!bands.length) return null;
  const vybrane =
    bands.length <= MAX_PAINT_STOPS
      ? bands
      : (() => {
          const out = [bands[0]];
          const krok = (bands.length - 1) / (MAX_PAINT_STOPS - 1);
          for (let i = 1; i < MAX_PAINT_STOPS - 1; i += 1) out.push(bands[Math.round(i * krok)]);
          out.push(bands[bands.length - 1]);
          const seen = new Set();
          return out.filter(([od]) => (seen.has(od) ? false : seen.add(od)));
        })();
  return vybrane.map(([od, doZ, v], i) => [
    od,
    i + 1 < vybrane.length ? vybrane[i + 1][0] - 1 : doZ,
    v
  ]);
}

/** Preriedi zlomy na strop – krajné ostávajú, vnútorné sa vyberú rovnomerne. */
function thin(stops) {
  if (stops.length <= MAX_PAINT_STOPS) return stops.map(([z, v]) => [z, v]);
  const out = [stops[0]];
  const step = (stops.length - 1) / (MAX_PAINT_STOPS - 1);
  for (let i = 1; i < MAX_PAINT_STOPS - 1; i += 1) out.push(stops[Math.round(i * step)]);
  out.push(stops[stops.length - 1]);
  // Preriedenie môže tú istú stopu vybrať dvakrát – dva zlomy na jednom zoome
  // by `normalizeOverrides` odmietol a s ním celú vlastnosť.
  const seen = new Set();
  return out.filter(([z]) => (seen.has(z) ? false : seen.add(z)));
}

/**
 * Odfotí vzhľad vrstvy tak, ako je NAOZAJ v mape.
 *
 * @param {object} layer  vrstva z hotového štýlu (už s úpravami)
 * @param {object} [o]    úprava tej vrstvy – nesie to, čo sa zo `paint`
 *                        prečítať nedá: „bez výplne", vzor a okraj
 * @returns {{from: string, label: string, type: string, paint: object,
 *            dash?: string, pattern?: object|null, outline?: object,
 *            icon?: string, dropped: string[]}}
 */
export function snapshotStyle(layer, o = {}) {
  const paint = {};
  const dropped = [];
  for (const [prop, value] of Object.entries(layer.paint || {})) {
    if (!SUFFIXES.some((s) => prop.endsWith(s))) continue;
    // „Bez výplne" je v štýle priehľadná farba – v úpravách má vlastné slovo.
    const own = (o.paint || {})[prop];
    const snap = own === NO_FILL ? NO_FILL : snapValue(value);
    if (snap === null) dropped.push(prop);
    else paint[prop] = snap;
  }

  const out = {
    from: layer.id,
    label: (layer.metadata || {})["frico:label"] || layer.id,
    type: layer.type,
    paint,
    dropped
  };

  const dasharray = (layer.paint || {})["line-dasharray"];
  // Plná čiara sa odfotí AKO „solid", nie ako prázdno – „sprav túto vrstvu
  // takú, ako je tamtá" musí vedieť aj zrušiť prerušovanie, ktoré má cieľová
  // vrstva zo štýlu (železnica, brod). Len pri čiare: na ploche by „solid"
  // nebola odpoveď na nič.
  const dash = o.dash || dashIdOf(dasharray)
    || (layer.type === "line" && !Array.isArray(dasharray) ? "solid" : null);
  if (dash) out.dash = dash;
  // Prerušovanie, ktoré nie je ani jedna z predvolieb (štýl si ho môže napísať
  // vlastné), sa do úprav zapísať nedá – a mlčať o tom by znamenalo, že vložená
  // vrstva vyzerá inak než tá, z ktorej sa kopírovalo.
  else if (Array.isArray(dasharray)) dropped.push("line-dasharray");

  // Vzor môže byť zabudovaný v štýle (`frico:pattern`) alebo z úpravy –
  // odfotiť treba ten ÚČINNÝ, inak by sa kopírovanie zo skalnej plochy tvárilo,
  // že žiadny vzor nemá.
  const builtin = (layer.metadata || {})["frico:pattern"] || null;
  const pattern = o && "pattern" in o ? o.pattern : builtin;
  if (pattern) out.pattern = { ...pattern };

  if (o.outline) out.outline = { ...o.outline };

  const icon = (layer.layout || {})["icon-image"];
  if (layer.type === "symbol" && typeof icon === "string") out.icon = icon;

  return out;
}

/** Hlavná vlastnosť danej prípony – tá, ktorá nie je halo ani obrys. */
function primaryOf(paint, suffix) {
  const props = Object.keys(paint).filter((p) => p.endsWith(suffix));
  return props.find((p) => !p.includes("halo") && !p.includes("outline")) || props[0] || null;
}

/**
 * Preloží odfotený vzhľad na inú vrstvu.
 *
 * Rovnaký druh vrstvy dostane všetko, čo pozná. Iný druh dostane HLAVNÚ
 * vlastnosť každej prípony preloženú na svoju predponu (`fill-color` →
 * `line-color`) – „nech je čiara taká zelená ako tá plocha" je zmysluplné
 * zadanie, „nech má čiara `fill-outline-color`" nie je.
 *
 * @returns {{patch: object, skipped: string[]}} `patch` ide do `setLayerOverride`
 */
export function pasteStyle(snap, target) {
  const allowed = new Set(ALLOWED[target.type] || []);
  const paint = {};
  const skipped = [];

  if (snap.type === target.type) {
    for (const [prop, value] of Object.entries(snap.paint)) {
      if (allowed.has(prop)) paint[prop] = value;
      else skipped.push(prop);
    }
  } else {
    const prefix = PREFIX[target.type];
    const used = new Set();
    for (const suffix of SUFFIXES) {
      const from = primaryOf(snap.paint, suffix);
      if (!from) continue;
      used.add(from);
      const to = prefix ? `${prefix}${suffix}` : null;
      if (to && allowed.has(to)) paint[to] = snap.paint[from];
      else skipped.push(from);
    }
    // Všetko ostatné (halo, obrys výplne) je vlastnosť TOHO druhu vrstvy –
    // na inom druhu nemá kam sadnúť a mlčky zmiznúť nesmie.
    for (const prop of Object.keys(snap.paint)) if (!used.has(prop)) skipped.push(prop);
  }

  // „Bez výplne" má zmysel len tam, kde je čo nevyplniť; na čiare by ju
  // `normalizeOverrides` odmietol a vypísal chybu.
  for (const [prop, value] of Object.entries(paint)) {
    if (value !== NO_FILL) continue;
    if (prop === "fill-color" || prop === "fill-extrusion-color") continue;
    delete paint[prop];
    skipped.push(prop);
  }

  const patch = {};
  if (Object.keys(paint).length) patch.paint = paint;

  if (snap.dash) {
    if (target.type === "line") patch.dash = snap.dash;
    // „Plná" na inú než čiarovú vrstvu nie je strata – tá o prerušovaní
    // nevie a v zozname „neprenieslo sa" by len mýlila.
    else if (snap.dash !== "solid") skipped.push("prerušovanie čiary");
  }
  if (snap.pattern) {
    if (canDecorate(target)) patch.pattern = { ...snap.pattern };
    else skipped.push("vzor");
  }
  if (snap.outline) {
    if (canDecorate(target)) patch.outline = { ...snap.outline };
    else skipped.push("okraj");
  }
  if (snap.icon) {
    if (target.type === "symbol" && typeof (target.layout || {})["icon-image"] === "string") {
      patch.icon = snap.icon;
    } else {
      skipped.push("ikona");
    }
  }

  return { patch, skipped: [...new Set(skipped)] };
}
