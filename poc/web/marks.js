/**
 * TURISTICKÁ A CYKLISTICKÁ ZNAČKA – to, čo je na strome (SK aj CZ).
 *
 * Turistické značenie u nás aj v Česku má JEDEN tvar: štvorec 10 × 10 cm,
 * v ňom vodorovný pás vo farbe trasy a nad ním aj pod ním pás podkladu.
 * Podklad je pri pešej značke biely, pri cyklistickej žltý; okrem pásovej
 * značky sú aj tvarové (trojuholník na vrchol, „L" k zrúcanine, kruh
 * k prameňu) a česká cyklistická značka má na žltom podklade bicykel.
 *
 * PREČO NIE IKONA ZO SADY. Doteraz sa pozdĺž trasy kreslila ikonka zo sady
 * ikoniek (horský štít pri turistickej, bicykel pri cyklotrase). Tá povie
 * DRUH trasy, ale nie to, čo človek v teréne hľadá – akú značku má sledovať.
 * „Choď po červenej" sa z ikonky horského štítu nedá prečítať; z červeného
 * pásu na bielom štvorci áno, lebo je to tá istá vec, aká je namaľovaná na
 * strome.
 *
 * PREČO HOTOVÝ FAREBNÝ OBRÁZOK A NIE SDF. Značka má TRI farby naraz
 * (podklad, pás, tenký lem) a SDF vie zafarbiť jednu (`icon-color`) plus
 * jedno halo. Je to tá istá úvaha, akou skončil ako pečený obrázok štítok
 * cesty (`poc/web/shields.js`) – len tu je dôvod ešte silnejší: pás a podklad
 * sú dva vnorené tvary, nie tvar a jeho obrys.
 *
 * A PRETO SA FARBY ZNAČIEK NEMENIA S TÉMOU. Značka je obrázok naozajstnej
 * tabuľky, nie prvok mapy: červená značka je červená aj na tmavej mape,
 * inak by prestala byť tým, čo je v teréne. Pásik trasy pod ňou naopak témou
 * ide (`trailRed` a spol. v `themes.js`) – tam je farba prvkom mapy. Kto by
 * chcel značky témovať, zaplatí to štvornásobkom obrázkov v sprite.
 *
 * KOMBINÁCIE SÚ VYMENOVANÉ, NIE VŠETKY. Podklad × farba × tvar by bolo
 * niekoľko stoviek obrázkov a väčšina z nich neexistuje ani v teréne
 * (červený pás na červenom podklade je prázdny štvorec). `MARK_FACES` je
 * preto zoznam dvojíc, ktoré na značkách naozaj sú – a `workers/lint/marks.mjs`
 * stráži, že `workers/trails/routes.py` inú dvojicu do dlaždíc nenapíše;
 * meno obrázka totiž skladá štýl z dát (`concat`) a obrázok, ktorý v sprite
 * nie je, MapLibre ticho nenakreslí.
 *
 * Tento súbor je LEN OBRÁZOK: aké tvary existujú, ako sa kreslia a ako sa
 * volajú. Ktorá trasa akú značku dostane, rozhoduje `workers/trails/routes.py`
 * (z `osmc:symbol`), a kedy sa kreslí, `poc/web/themes.js`. Pečie ich do
 * spritu `workers/assets/marks.mjs`.
 */

/**
 * Farby značiek. Sú to farby NÁTERU na strome, nie farby mapy – preto sú
 * tu, a nie v palete tém. Odtiene sú z fotiek značiek a z toho, čo KČT
 * (SK aj CZ značenie robí tá istá organizácia) používa na svojich mapách;
 * blízko k `trail*` farbám svetlej témy, aby pásik a značka nad ním
 * nevyzerali ako dve rôzne trasy.
 */
export const MARK_COLOURS = {
  red: "#d42a2a",
  blue: "#2a54c8",
  green: "#1f8a3c",
  yellow: "#f2c200",
  black: "#2a2a2a",
  white: "#ffffff"
};

export const MARK_COLOUR_IDS = Object.keys(MARK_COLOURS);

/**
 * Dvojice PODKLAD → FARBA ZNAČKY, ktoré sa pečú.
 *
 * Prvé dva riadky sú to, čo je v teréne: biely podklad s farebným pásom je
 * pešia značka, žltý je cyklistická (a v Česku aj lyžiarska). Tretí riadok
 * sú OBRÁTENÉ značky – náučný chodník má biely pás na farebnom podklade
 * (`osmc:symbol=green:green:white_bar`), takže bez neho by sa kreslil biely
 * pás na bielom štvorci, teda nič.
 *
 * Rovnaká farba pásu ako podkladu tu byť NESMIE: taká značka je prázdny
 * štvorec a v mape vyzerá ako chyba vykreslenia. Stráži to lint.
 */
export const MARK_FACES = [
  ["white", "red"], ["white", "blue"], ["white", "green"],
  ["white", "yellow"], ["white", "black"],
  ["yellow", "red"], ["yellow", "blue"], ["yellow", "green"],
  ["yellow", "black"], ["yellow", "white"],
  ["red", "white"], ["blue", "white"], ["green", "white"], ["black", "white"]
];

/**
 * Tvary značiek.
 *
 * KTORÁ HODNOTA `osmc:symbol` znamená KTORÝ tvar, tu zámerne NIE JE: to je
 * otázka o tom, čo je v OSM napísané, a odpovedá na ňu `OSMC_SHAPES` vo
 * `workers/trails/routes.py` – tam, kde sa tag číta. Tu je len to, ako tvar
 * vyzerá. `workers/lint/marks.mjs` stráži, že routes.py nepošle do dlaždíc
 * tvar, ktorý tu nie je (v mape by po ňom ostalo prázdno).
 *
 * `label` je pre developer mode (dá sa v ňom tvar značky prepnúť ručne).
 */
export const MARK_SHAPES = [
  {
    id: "bar",
    label: "Pásová (vodorovný pás)",
    draw: (u, v) => v >= 0.34 && v <= 0.66
  },
  {
    id: "slash",
    label: "Šikmý pás",
    // `backslash` sa kreslí tým istým tvarom: v mierke, kde má značka 14 px,
    // je smer šikmého pásu nerozoznateľný a druhý obrázok by nič nepridal.
    draw: (u, v) => Math.abs(u + v - 1) <= 0.22
  },
  {
    id: "triangle",
    label: "Vrcholová (trojuholník)",
    draw: (u, v) => v >= 0.16 && v <= 0.84 &&
      Math.abs(u - 0.5) <= ((v - 0.16) / 0.68) * 0.42
  },
  {
    id: "circle",
    label: "Kruh (prstenec)",
    draw: (u, v) => {
      const r = Math.hypot(u - 0.5, v - 0.5);
      return r >= 0.24 && r <= 0.40;
    }
  },
  {
    id: "dot",
    label: "Plný kruh",
    draw: (u, v) => Math.hypot(u - 0.5, v - 0.5) <= 0.32
  },
  {
    id: "corner",
    label: "„L“ (odbočka k pamiatke)",
    draw: (u, v) => (u >= 0.14 && u <= 0.42 && v >= 0.14 && v <= 0.86) ||
      (v >= 0.58 && v <= 0.86 && u >= 0.14 && u <= 0.86)
  },
  {
    id: "bowl",
    label: "„U“ (odbočka k prameňu)",
    draw: (u, v) => {
      const inRing = (() => {
        const r = Math.hypot(u - 0.5, v - 0.58);
        return r >= 0.20 && r <= 0.36 && v >= 0.58;
      })();
      const legs = Math.abs(Math.abs(u - 0.5) - 0.28) <= 0.08 && v <= 0.58 && v >= 0.16;
      return inRing || legs;
    }
  },
  {
    id: "cross",
    label: "Kríž",
    draw: (u, v) => (Math.abs(u - 0.5) <= 0.12 && Math.abs(v - 0.5) <= 0.36) ||
      (Math.abs(v - 0.5) <= 0.12 && Math.abs(u - 0.5) <= 0.36)
  },
  {
    id: "x",
    label: "Kríž šikmý (X)",
    draw: (u, v) => {
      const inSquare = u >= 0.14 && u <= 0.86 && v >= 0.14 && v <= 0.86;
      return inSquare && (Math.abs(u - v) <= 0.17 || Math.abs(u + v - 1) <= 0.17);
    }
  },
  {
    id: "diamond",
    label: "Kosoštvorec",
    draw: (u, v) => Math.abs(u - 0.5) + Math.abs(v - 0.5) <= 0.40
  },
  {
    id: "bicycle",
    // Česká (a slovenská mestská) cyklistická značka: žltý štvorec s čiernym
    // bicyklom. Kreslí sa z dvoch kolies a rámu – v 14 pixeloch je detailnejší
    // bicykel aj tak nerozoznateľný od škvrny.
    label: "Bicykel (cyklistická značka)",
    draw: (u, v) => {
      const wheel = (cx) => {
        const r = Math.hypot(u - cx, v - 0.64);
        return r >= 0.13 && r <= 0.22;
      };
      const seg = (ax, ay, bx, by, w) => {
        const dx = bx - ax;
        const dy = by - ay;
        const t = Math.max(0, Math.min(1, ((u - ax) * dx + (v - ay) * dy) / (dx * dx + dy * dy)));
        return Math.hypot(u - (ax + t * dx), v - (ay + t * dy)) <= w;
      };
      return wheel(0.23) || wheel(0.77) ||
        seg(0.23, 0.64, 0.5, 0.64, 0.05) || seg(0.5, 0.64, 0.77, 0.64, 0.05) ||
        seg(0.5, 0.64, 0.42, 0.34, 0.05) || seg(0.42, 0.34, 0.68, 0.34, 0.05);
    }
  }
];

export const MARK_SHAPE_IDS = MARK_SHAPES.map((s) => s.id);
export const DEFAULT_MARK_SHAPE = "bar";

/** Tvar podľa id; pri neznámom id vráti pásovú značku. */
export function markShape(id) {
  return MARK_SHAPES.find((s) => s.id === id) || MARK_SHAPES[0];
}

/**
 * Meno obrázka v sprite. Skladá ho aj štýl – ale výrazom nad dátami
 * (`concat`), takže tu je to isté meno ako funkcia a lint porovnáva, že sa
 * tie dve cesty k nemu zhodujú.
 */
export const MARK_PREFIX = "mark-";
export function markImage(bg, fg, shape) {
  return `${MARK_PREFIX}${bg}-${fg}-${shape}`;
}

/** Všetky obrázky, ktoré sa pečú: dvojice × tvary. */
export function markImages() {
  const out = [];
  for (const [bg, fg] of MARK_FACES) {
    for (const shape of MARK_SHAPES) {
      out.push({ name: markImage(bg, fg, shape.id), bg, fg, shape });
    }
  }
  return out;
}

/**
 * Strana štvorca značky v pixeloch pri `pixelRatio` 1 (bez lemu a okraja).
 *
 * 14 px nie je od oka: pri z16 je pásik trasy 2,6 px široký a značka má byť
 * to, čo pri nej na prvý pohľad vidno – teda výrazne väčšia, ale nie taká,
 * aby zakryla cestu pod sebou. `icon-size` v štýle ju od z13 po z20 škáluje.
 */
export const MARK_BOX = 14;

/**
 * Od akého zoomu má značka zmysel.
 *
 * Je tu, a nie v štýle, lebo sa na ňu pýtajú DVE miesta: `themes.js` (odkiaľ
 * vrstva začína) a `map-types.js` (turistická mapa púšťa pásiky trás už od
 * z8 a značky pri tom nesmie stiahnuť so sebou). Pod z12 je zo značky škvrna
 * a je ich toľko, koľko trás – z mapy by bol šum.
 */
export const MARK_MINZOOM = 12;
/** Tenký lem okolo štvorca – bez neho biela značka na svetlej mape zmizne. */
export const MARK_EDGE = "#00000055";
/** Hrúbka lemu v pixeloch pri `pixelRatio` 1. */
export const MARK_EDGE_W = 1;

/** `#rrggbb` (aj s alfou) → `[r, g, b, a]`, a je 0–1. */
function rozlozFarbu(hex) {
  const h = String(hex || "#000000").replace("#", "");
  const n = h.length === 3 || h.length === 4
    ? h.split("").map((ch) => ch + ch).join("")
    : h;
  const v = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16) || 0);
  const a = n.length >= 8 ? (parseInt(n.slice(6, 8), 16) || 0) / 255 : 1;
  return [v[0], v[1], v[2], a];
}

/**
 * Vykreslí značku ako HOTOVÝ FAREBNÝ OBRÁZOK (RGBA, nie SDF).
 *
 * Tvary sú predikáty nad jednotkovým štvorcom (`draw(u, v)`), nie cesty:
 * vyhladenie hrán je preto obyčajné 4 × 4 prevzorkovanie na pixel a nový
 * tvar je jeden riadok podmienky. Pri 154 obrázkoch veľkosti 16 px je to
 * pár milisekúnd.
 *
 * @param {object} shape       položka z `MARK_SHAPES`
 * @param {string} bgHex       farba podkladu
 * @param {string} fgHex       farba pásu / tvaru
 * @param {number} pixelRatio  1 alebo 2 (varianta @2x)
 * @returns {{width:number, height:number, data:Uint8Array}}
 */
export function renderMark(shape, bgHex, fgHex, pixelRatio = 1) {
  const r = pixelRatio;
  const box = Math.round(MARK_BOX * r);
  const edge = MARK_EDGE_W * r;
  // Pixel priehľadného okraja: bez neho by sa pri škálovaní hrana obrázka
  // „oprela" o susedný obrázok v atlase a v mape by po nej tiekla jeho farba.
  const pad = Math.max(1, Math.round(r));
  const size = box + 2 * pad;

  const bg = rozlozFarbu(bgHex);
  const fg = rozlozFarbu(fgHex);
  const ed = rozlozFarbu(MARK_EDGE);
  const data = new Uint8Array(size * size * 4);

  const SS = 4;                       // prevzorkovanie na pixel (4 × 4)
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      let vnutri = 0;                 // koľko vzoriek padlo do štvorca
      let pas = 0;                    // z toho do tvaru značky
      let lem = 0;                    // z toho do lemu
      for (let sy = 0; sy < SS; sy += 1) {
        for (let sx = 0; sx < SS; sx += 1) {
          const px = x + (sx + 0.5) / SS - pad;
          const py = y + (sy + 0.5) / SS - pad;
          if (px < 0 || py < 0 || px >= box || py >= box) continue;
          vnutri += 1;
          if (px < edge || py < edge || px >= box - edge || py >= box - edge) {
            lem += 1;
            continue;
          }
          if (shape.draw(px / box, py / box)) pas += 1;
        }
      }
      const n = SS * SS;
      const aVnutri = vnutri / n;
      if (!aVnutri) continue;
      const wLem = lem / n;
      const wPas = pas / n;
      const wPod = aVnutri - wLem - wPas;

      // Lem je poloprehľadný, takže leží NAD podkladom – zmieša sa s ním,
      // nie s mapou pod značkou (tam by z bielej značky vznikol sivý rám).
      const lemR = ed[0] * ed[3] + bg[0] * (1 - ed[3]);
      const lemG = ed[1] * ed[3] + bg[1] * (1 - ed[3]);
      const lemB = ed[2] * ed[3] + bg[2] * (1 - ed[3]);

      const i = (y * size + x) * 4;
      data[i] = Math.round((bg[0] * wPod + fg[0] * wPas + lemR * wLem) / aVnutri);
      data[i + 1] = Math.round((bg[1] * wPod + fg[1] * wPas + lemG * wLem) / aVnutri);
      data[i + 2] = Math.round((bg[2] * wPod + fg[2] * wPas + lemB * wLem) / aVnutri);
      data[i + 3] = Math.round(255 * aVnutri);
    }
  }

  return { width: size, height: size, data };
}
