/**
 * Developer mode – ladenie mapy priamo v prehliadači.
 *
 * Čo vie:
 *   - vypísať **všetky** vrstvy štýlu po skupinách, s druhom (plocha / línia /
 *     bod / popisok / 3D / reliéf) a filtrom, zapnúť/vypnúť ich a nastaviť im
 *     rozsah zoomu – teda presne definovať, čo sa kedy zobrazuje,
 *   - robiť to **zvlášť pre každý typ mapy** (turistická, lyžiarska, cestná,
 *     historická, základná): prepínač *len táto mapa / všetky mapy* hovorí,
 *     kam sa úprava zapíše, takže „na cestnej mape toto nechcem" nemusí
 *     znamenať „nikde to nechcem",
 *   - prezerať mapu po zoomoch: nastavíš zoom a zoznam ukáže, ktoré vrstvy
 *     sú na ňom naozaj povolené a ktoré sa orežú – a jedným klikom sa dá na
 *     tom zoome vrstva zapnúť či vypnúť (pásik zoomov z0–z20 v detaile
 *     vrstvy, alebo štítok s rozsahom priamo v riadku),
 *   - kliknúť do mapy a dostať **všetko, čo je pod kurzorom** – každý prvok
 *     zo všetkých vrstiev naraz, aj so všetkými atribútmi z dlaždice, plus
 *     zoznam značených trás, ktoré tadiaľ vedú (ich pásiky sú posunuté vedľa
 *     cesty, takže do nich klik netrafí),
 *   - zmeniť farbu ktoréhokoľvek prvku: farby vrstvy zvlášť aj celej palety
 *     naraz, vrátane hromadnej editácie výberu a kopírovania hodnôt – a to aj
 *     tam, kde si vrstva farbu vyberá **výrazom** (pásik trasy podľa značky
 *     z OSM): tie sa ladia z palety priamo v riadku vrstvy,
 *   - vymeniť **ikonu** symbolovej vrstvy za ktorúkoľvek zo sady,
 *   - dať ploche alebo čiare opakujúci sa **vzor** a ľubovoľný **okraj**,
 *     čiare aj prerušovanie,
 *   - skryť konkrétne triedy POI a prepnúť celú sadu ikoniek,
 *   - **vrátiť vrstvu do pôvodného stavu celú** – nie len farbu po farbe:
 *     jedno tlačidlo v detaile vrstvy zahodí viditeľnosť, zoomy, farby,
 *     hrúbky, ikonu, vzor aj okraj (v tom rozsahu, ktorý je práve zvolený),
 *   - **skopírovať vzhľad jednej vrstvy do druhej** („nech sú múry ako
 *     ploty"): odfotí sa to, čo je v štýle NAOZAJ, nie len úprava nad ním,
 *     takže kopírovať sa dá aj z vrstvy, ktorú nikto neladil. Čo sa na cieľ
 *     nezmestí (obrys výplne na čiaru), sa nevloží a povie sa to,
 *   - všetko priebežne ukladá do prehliadača (localStorage), vie to
 *     exportovať do `style-overrides.json` a znovu načítať.
 *
 * Ten istý JSON potom prevezme workflow „Mapa · úpravy štýlu",
 * uloží ho do repozitára a pipeline ho zapečie do mapy pre web aj iOS.
 */
import {
  THEMES,
  PALETTE_GROUPS,
  PALETTE_LABELS,
  LAYER_GROUPS,
  LAYER_KINDS,
  MAX_DISPLAY_Z,
  MAX_PAINT_STOPS,
  NO_FILL,
  sortStops,
  sortBands,
  isBandList,
  emptyOverrides,
  normalizeOverrides,
  hasOverrides,
  mergedPalette,
  selectedIconSource,
  TRAIL_TYPES,
  TRAIL_MARK_COLOURS,
  TRAIL_GAP_DEFAULTS,
  TRAIL_GAP_ZOOM,
  TRAIL_MARK_DEFAULTS,
  TRAIL_MARK_ZOOM,
  SHIELD_DEFS,
  trailGapPx,
  trailMarkPx,
  trailTypeDef,
  shieldShapeFor,
  DEFAULT_MAP_TYPE,
  mapTypeDef,
  mapTypeHidden,
  normalizeMapType
} from "./themes.js";
import { PATTERNS, DASH_PRESETS, dashPreview } from "./patterns.js";
import { MARK_SHAPES } from "./marks.js";
import { SHIELD_SHAPES } from "./shields.js";
import { snapshotStyle, pasteStyle, valueAtZoom } from "./layer-style.js";

const STORAGE_KEY = "fricomaps.overrides";
const SCOPE_KEY = "fricomaps.devscope";
const KIND_LABELS = Object.fromEntries(LAYER_KINDS.map((k) => [k.id, k.label]));
const GROUP_LABELS = Object.fromEntries(LAYER_GROUPS.map((g) => [g.id, g.label]));
/** Meno značky z dlaždíc → kľúč palety (rovnako ako v štýle). */
const TRAIL_MARK_KEYS = Object.fromEntries(TRAIL_MARK_COLOURS);

/**
 * Obrázky, ktoré si do spritu pečieme sami – značky trás (`mark-…`) a podklady
 * štítkov ciest (`shield-…`). V zoznamoch „vyber ikonu" nemajú čo robiť: nie sú
 * to symboly, ale hotové farebné obrázky konkrétnej veci, a je ich viac než
 * samotných ikoniek (154 značiek proti pár stovkám ikon). Prepínajú sa tam, kam
 * patria – v záložke „Trasy" a „Štítky".
 */
const BAKED_IMAGE = /^(mark|shield)-/;
const pickableIcons = (set, extra) =>
  [
    ...new Set([
      ...(extra ? [extra] : []),
      ...(set?.icons || []).filter((n) => !BAKED_IMAGE.test(n))
    ])
  ].sort();

/** Načíta úpravy uložené v prehliadači. */
export function loadOverrides() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyOverrides();
    return normalizeOverrides(JSON.parse(raw)).overrides;
  } catch {
    return emptyOverrides();
  }
}

/** Kam sa naposledy zapisovali úpravy: `map` (len táto mapa) alebo `all`. */
function loadScope() {
  try {
    return localStorage.getItem(SCOPE_KEY) === "all" ? "all" : "map";
  } catch {
    return "map";
  }
}

/** Uloží úpravy do prehliadača (používa aj hlavný panel viewra). */
export function saveOverrides(overrides) {
  try {
    if (hasOverrides(overrides)) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* súkromný režim – úpravy zostanú len do zatvorenia karty */
  }
}

/** JSON tak, ako sa ukladá do súboru aj do zdrojáku. */
export function serializeOverrides(overrides) {
  return JSON.stringify(
    { ...overrides, updated_at: new Date().toISOString() },
    null,
    2
  );
}

const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(children)) if (c) node.appendChild(c);
  return node;
};

const isHex6 = (v) => /^#[0-9a-f]{6}$/i.test(v);
/** Farba, akú berie štýl: `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`. */
const isHexColor = (v) =>
  /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(String(v || "").trim());
/** `input[type=color]` vie iba 6-miestny hex – kratšie zápisy dopočítame. */
const toHex6 = (v) => {
  const s = String(v || "").trim();
  if (isHex6(s)) return s.toLowerCase();
  const m = /^#([0-9a-f])([0-9a-f])([0-9a-f])([0-9a-f])?$/i.exec(s);
  if (m) return `#${m[1]}${m[1]}${m[2]}${m[2]}${m[3]}${m[3]}`.toLowerCase();
  const m8 = /^#([0-9a-f]{6})[0-9a-f]{2}$/i.exec(s);
  if (m8) return `#${m8[1]}`.toLowerCase();
  return "#000000";
};

/**
 * ALFA FARBY, ODDELENE OD ODTIEŇA – a je to kvôli tieňovaniu reliéfu.
 *
 * `hillShadow`, `hillHighlight` a `hillAccent` sú `#rrggbbaa` (prečo, je
 * v `themes.js` pri vrstve `hillshade`) a `input[type=color]` alfu nepozná:
 * vrátil by `#rrggbb`. Kým sa alfa nedržala bokom, stačilo ťuknúť do
 * farby tieňa a alfa TICHO zmizla – v mape z toho bola nepriehľadná plocha
 * cez celý svah a v `style-overrides.json` farba, ktorá vyzerá ako drobná
 * úprava odtieňa. Preto je alfa vlastná hodnota: pipetka mení odtieň
 * a alfu nesie ďalej, textové políčko ukazuje a berie celý zápis.
 */
const alphaOf = (v) => {
  const s = String(v || "").trim();
  const m8 = /^#[0-9a-f]{6}([0-9a-f]{2})$/i.exec(s);
  if (m8) return parseInt(m8[1], 16) / 255;
  const m4 = /^#[0-9a-f]{3}([0-9a-f])$/i.exec(s);
  if (m4) return parseInt(m4[1] + m4[1], 16) / 255;
  return 1;
};
/** Odtieň + alfa späť do jedného zápisu; pri plnej alfe ostáva `#rrggbb`. */
const withAlpha = (hex6, alpha) => {
  const a = Math.min(1, Math.max(0, Number(alpha)));
  if (!(a < 1)) return toHex6(hex6);
  return `${toHex6(hex6)}${Math.round(a * 255).toString(16).padStart(2, "0")}`;
};
/** Zápis farby, ako sa má uložiť do úprav (malými písmenami, bez medzier). */
const normColor = (v) => withAlpha(toHex6(v), alphaOf(v));

/** Stmaví farbu – použité ako predvolená farba vzoru a okraja. */
const darken = (hex, factor = 0.55) => {
  const h = toHex6(hex);
  const ch = (i) =>
    Math.round(parseInt(h.slice(1 + i * 2, 3 + i * 2), 16) * factor)
      .toString(16)
      .padStart(2, "0");
  return `#${ch(0)}${ch(1)}${ch(2)}`;
};

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Bez HTTPS/oprávnenia clipboard API nefunguje – fallback cez výber textu.
    const ta = el("textarea", { class: "dev-clip" });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch {
      /* nedá sa – používateľ si text skopíruje ručne z exportu */
    }
    ta.remove();
  }
  if (button) {
    const old = button.textContent;
    button.textContent = "✓";
    setTimeout(() => {
      button.textContent = old;
    }, 900);
  }
}

/** Rozsah zoomu vrstvy ako text. */
const zoomRangeText = (layer) => {
  const mn = layer.minzoom ?? 0;
  const mx = layer.maxzoom ?? MAX_DISPLAY_Z + 4;
  // Nad MAX_DISPLAY_Z sa priblížiť nedá, takže „do z21" a „bez hornej hranice"
  // je pre čitateľa to isté.
  const noTop = mx > MAX_DISPLAY_Z;
  if (mn <= 0 && noTop) return "vždy";
  if (noTop) return `z${mn}+`;
  return `z${mn}–${mx}`;
};

/** Kreslí sa vrstva na danom zoome? */
const activeAt = (layer, z) => {
  if ((layer.layout || {}).visibility === "none") return false;
  return z >= (layer.minzoom ?? 0) && z < (layer.maxzoom ?? 25);
};

/** Je vrstva vypnutá (nie orezaná zoomom)? */
const isHiddenLayer = (layer) => (layer.layout || {}).visibility === "none";

/**
 * Aký rozsah zoomu má vrstva mať, keď sa na zoome `z` má (ne)kresliť.
 *
 * Rozsah je jeden súvislý interval `<minzoom, maxzoom)`, takže „na tomto
 * zoome áno / nie" znamená posunúť jeho koniec:
 *   - zapnutie mimo rozsahu natiahne bližší koniec až po `z`,
 *   - vypnutie vnútri rozsahu ustúpi tým koncom, ktorý je bližšie.
 * Keď by z rozsahu nič neostalo, vráti `{ hide: true }` – vtedy je poctivejšie
 * vrstvu vypnúť než jej nastaviť prázdny interval.
 *
 * @returns {{minzoom?: number|undefined, maxzoom?: number|undefined, hide?: boolean, show?: boolean}}
 */
function zoomRangeFor(layer, z, on) {
  const mn = layer.minzoom ?? 0;
  const mx = layer.maxzoom ?? MAX_DISPLAY_Z + 1;
  const top = MAX_DISPLAY_Z + 1;

  if (on) {
    const patch = { show: true };
    if (z < mn) patch.minzoom = z;
    // `maxzoom` je horná hranica bez rovnosti – aby sa vrstva kreslila aj na
    // `z`, musí siahať o krok vyššie. Hodnotu treba zapísať aj na samom
    // vrchu: vrstva môže mať vlastný `maxzoom` zo štýlu (POI má 16), takže
    // „zmazať úpravu" by ju na z20 nezaplo.
    if (z >= mx) patch.maxzoom = Math.min(z + 1, top);
    return patch;
  }

  // Vrstva sa na tom zoome aj tak nekreslí – netreba nič meniť.
  if (z < mn || z >= mx) return {};
  // Bližší koniec ustúpi; pri rovnakej vzdialenosti sa oreže zospodu, lebo
  // mapa sa častejšie ladí smerom „od akého zoomu sa to objaví".
  if (z - mn <= mx - 1 - z) {
    return z + 1 >= mx ? { hide: true } : { minzoom: z + 1 };
  }
  return z <= mn ? { hide: true } : { maxzoom: z };
}

/**
 * @param {object} opts
 * @param {HTMLElement} opts.root      prázdny kontajner pre panel
 * @param {() => object} opts.getStyle aktuálny (už upravený) MapLibre štýl
 * @param {() => string} opts.getTheme kľúč aktuálnej témy
 * @param {() => string} [opts.getMapType] id práve zobrazeného typu mapy
 * @param {() => object} opts.getMap   inštancia mapy
 * @param {() => object[]} [opts.getIconSets] nasadené sady ikoniek
 * @param {(overrides: object) => void} opts.onChange  prekresli mapu
 */
export function initDevMode({
  root,
  getStyle,
  getTheme,
  getMapType,
  getMap,
  getIconSets,
  onChange
}) {
  let overrides = loadOverrides();
  let tab = "layers";
  let search = "";
  let kindFilter = new Set();
  let onlyActive = false;
  /** Ktorý typ mapy sa práve ladí (a teda kam patria úpravy „len táto mapa"). */
  const mapTypeId = () => normalizeMapType(getMapType ? getMapType() : DEFAULT_MAP_TYPE);
  /**
   * Kam sa zapisujú úpravy vrstiev a POI: `map` = len do práve zobrazeného
   * typu mapy, `all` = do spoločných, ktoré platia pre všetky.
   */
  let editScope = loadScope();
  let zoomView = getMap()?.getZoom?.() ?? 10;
  const selectedLayers = new Set();
  const selectedPaletteKeys = new Set();
  const collapsed = new Set();
  const expanded = new Set();
  let poiClasses = [];
  let applyTimer = null;
  let zoomTimer = null;
  /** Posledný výber z mapy (záložka Prvky) – `null`, kým sa neklikne. */
  let picked = null;
  /** Polomer výberu v pixeloch – čiara má šírku 2 px, trafiť ju treba vedieť. */
  let pickRadius = 6;
  const pickOpen = new Set();
  /**
   * Odfotený vzhľad vrstvy, ktorý čaká na vloženie do inej („kopírovať štýl
   * z jedného prvku do druhého"). Žije len v tejto relácii panelu – do
   * `style-overrides.json` nepatrí, je to schránka, nie nastavenie.
   */
  let styleClip = null;
  /** Čo sa pri poslednom vložení nedalo preniesť – vypíše to stavový riadok. */
  let clipNote = "";

  // ---------- základná kostra ----------
  const body = el("div", { class: "dev-body" });
  const status = el("div", { class: "dev-status" });
  const tabsBar = el("div", { class: "dev-tabs" });

  const TABS = [
    ["layers", "Vrstvy"],
    ["pick", "Prvky"],
    ["palette", "Paleta"],
    ["trails", "Trasy"],
    ["shields", "Štítky"],
    ["icons", "Ikony"],
    ["poi", "POI"],
    ["file", "Súbor"]
  ];

  /** Bitmapy spritov pre náhľad ikoniek – načítajú sa raz. */
  const spriteImages = new Map();

  root.appendChild(
    el("div", { class: "dev-head" }, [
      el("b", { text: "🛠 Developer mode" }),
      el("button", {
        class: "dev-x",
        type: "button",
        title: "Zavrieť",
        text: "✕",
        onclick: () => root.dispatchEvent(new CustomEvent("dev-close", { bubbles: true }))
      })
    ])
  );
  root.appendChild(tabsBar);
  root.appendChild(body);
  root.appendChild(status);

  // Posuvník zoomu sleduje mapu, takže zoznam vždy ukazuje, čo je naozaj
  // povolené na tom zoome, na ktorom sa práve pozeráme.
  const map = getMap();
  if (map) {
    map.on("zoomend", () => {
      zoomView = map.getZoom();
      if (tab !== "layers") return;
      if (zoomTimer) clearTimeout(zoomTimer);
      zoomTimer = setTimeout(renderBody, 120);
    });

    // Inšpektor berie klik len s otvorenou záložkou „Prvky" – inak by
    // developer mode potichu zobral kliknutie popupu s POI.
    map.on("click", (ev) => {
      if (tab !== "pick") return;
      pickAt(ev.point, ev.lngLat);
    });

    // Po každej zmene štýlu (farba, vrstva, prepnutie témy) sa vrstvy
    // zvýraznenia stratia – `setStyle` zmaže všetko, čo v novom štýle nie je.
    // Doplnenie ide cez timeout, aby prebehlo až po dokončení zmeny; podmienka
    // `getLayer` v `restoreHighlight` zároveň bráni zacykleniu (`addLayer`
    // vyvolá `styledata` znova).
    let restoreTimer = null;
    map.on("styledata", () => {
      if (!picked || map.getLayer(`${HL_PREFIX}-line`)) return;
      if (restoreTimer) clearTimeout(restoreTimer);
      restoreTimer = setTimeout(restoreHighlight, 60);
    });
  }

  // ---------- výber prvkov v mape (záložka Prvky) ----------
  // Mapa je poskladaná z desiatok vrstiev nad sebou: na jednom mieste býva
  // plocha, cesta, jej obrys, vrstevnica, pásik trasy aj popisok. Inšpektor
  // vypíše **všetko**, čo je pod kurzorom, aj so všetkými atribútmi – takže
  // je vidieť, z ktorej vrstvy to je a čo v dlaždici naozaj stojí.
  const HL_SOURCE = "__dev-pick";
  const HL_PREFIX = "__dev-pick";
  const EMPTY_FC = { type: "FeatureCollection", features: [] };

  /** Vrstvy zvýraznenia. Nie sú v štýle – pridávajú sa priamo do mapy. */
  const HL_LAYERS = [
    {
      id: `${HL_PREFIX}-fill`,
      type: "fill",
      source: HL_SOURCE,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": "#ff7a00", "fill-opacity": 0.25 }
    },
    {
      id: `${HL_PREFIX}-line`,
      type: "line",
      source: HL_SOURCE,
      filter: ["!=", ["geometry-type"], "Point"],
      paint: { "line-color": "#ff7a00", "line-width": 3.5, "line-opacity": 0.9 }
    },
    {
      id: `${HL_PREFIX}-point`,
      type: "circle",
      source: HL_SOURCE,
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-radius": 7,
        "circle-color": "#ff7a00",
        "circle-opacity": 0.35,
        "circle-stroke-color": "#ff7a00",
        "circle-stroke-width": 2
      }
    }
  ];

  /**
   * Zvýraznenie musí prežiť prekreslenie štýlu: `setStyle` porovná starý
   * a nový štýl a čokoľvek, čo v novom nie je (teda aj tieto vrstvy), zmaže.
   */
  function ensureHighlight(m) {
    if (!m.getSource(HL_SOURCE)) {
      m.addSource(HL_SOURCE, { type: "geojson", data: EMPTY_FC });
    }
    for (const layer of HL_LAYERS) if (!m.getLayer(layer.id)) m.addLayer(layer);
  }

  /** @returns {boolean} podarilo sa? (počas prekresľovania štýlu nie) */
  function setHighlight(features) {
    const m = getMap();
    if (!m) return false;
    try {
      ensureHighlight(m);
      m.getSource(HL_SOURCE).setData({
        type: "FeatureCollection",
        features: features.map((f) => ({
          type: "Feature",
          geometry: f.geometry,
          properties: {}
        }))
      });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Vráti zvýraznenie po prekreslení štýlu. Keď mapa práve nie je v stave, kedy
   * sa dá pridať vrstva (plné načítanie štýlu je asynchrónne), skúsi to znova,
   * až keď sa mapa upokojí.
   */
  function restoreHighlight() {
    const m = getMap();
    if (!m || !picked || m.getLayer(`${HL_PREFIX}-line`)) return;
    if (!setHighlight(picked.features)) m.once("idle", restoreHighlight);
  }

  /** Ktoré vrstvy v mape patria danému zdroju (a naozaj v nej sú). */
  const layersOfSource = (m, source, type) =>
    (getStyle()?.layers || [])
      .filter((l) => l.source === source && (!type || l.type === type) && m.getLayer(l.id))
      .map((l) => l.id);

  /** Poradie vrstvy v štýle – podľa neho sa výsledky radia zhora nadol. */
  function layerOrder() {
    const order = new Map();
    (getStyle()?.layers || []).forEach((l, i) => order.set(l.id, i));
    return order;
  }

  function pickAt(point, lngLat) {
    const m = getMap();
    if (!m) return;
    const r = pickRadius;
    const box = [
      [point.x - r, point.y - r],
      [point.x + r, point.y + r]
    ];
    let all = [];
    try {
      all = m.queryRenderedFeatures(box);
    } catch {
      all = [];
    }
    all = all.filter((f) => !String(f.layer?.id || "").startsWith(HL_PREFIX));

    // Ten istý prvok býva v niekoľkých dlaždiciach (rozrezaný na hranici),
    // takže by sa v zozname zopakoval.
    const seen = new Set();
    const feats = [];
    for (const f of all) {
      const key = `${f.layer.id}|${f.id ?? ""}|${JSON.stringify(f.properties)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      feats.push(f);
    }

    // Pásiky značených trás sú posunuté VEDĽA cesty (`line-offset`), takže
    // klik do cesty ich netrafí. Hľadajú sa preto v širšom okolí a vypisujú
    // sa zvlášť: „ktoré trasy tadiaľto vedú" je iná otázka než „na čo som
    // presne klikol".
    const trailIds = layersOfSource(m, "trails", "line");
    const wide = r + 18;
    let trails = [];
    if (trailIds.length) {
      try {
        trails = m.queryRenderedFeatures(
          [
            [point.x - wide, point.y - wide],
            [point.x + wide, point.y + wide]
          ],
          { layers: trailIds }
        );
      } catch {
        trails = [];
      }
    }
    const byRel = new Map();
    for (const f of trails) {
      const p = f.properties || {};
      const key = p.rel ?? `${p.route}|${p.colour}|${p.name}|${p.ref}`;
      if (!byRel.has(key)) byRel.set(key, p);
    }

    const order = layerOrder();
    feats.sort((a, b) => (order.get(b.layer.id) ?? 0) - (order.get(a.layer.id) ?? 0));

    pickOpen.clear();
    picked = {
      lngLat: { lng: lngLat.lng, lat: lngLat.lat },
      zoom: m.getZoom(),
      features: feats,
      trails: [...byRel.values()]
    };
    setHighlight(feats);
    if (tab === "pick") renderBody();
  }

  function clearPick() {
    picked = null;
    pickOpen.clear();
    setHighlight([]);
    renderBody();
  }

  function renderTabs() {
    tabsBar.replaceChildren(
      ...TABS.map(([id, label]) =>
        el("button", {
          type: "button",
          class: `dev-tab${tab === id ? " on" : ""}`,
          text: label,
          onclick: () => {
            tab = id;
            render();
          }
        })
      )
    );
  }

  // ---------- ukladanie a prekreslenie ----------
  function apply({ rerender = true, immediate = false } = {}) {
    saveOverrides(overrides);
    renderStatus();
    const run = () => {
      applyTimer = null;
      onChange(overrides);
      if (rerender) render();
    };
    // Aj „okamžité" použitie ide cez timeout: zmeny prichádzajú z change/blur
    // handlerov a prekresliť panel priamo v nich Chromium neznesie.
    if (applyTimer) clearTimeout(applyTimer);
    applyTimer = setTimeout(run, immediate ? 0 : 90);
  }

  function renderStatus() {
    const nPalette = Object.values(overrides.palette).reduce(
      (n, c) => n + Object.keys(c).length,
      0
    );
    const nLayers = Object.keys(overrides.layers).length;
    const nPoi = overrides.poi.hidden.length;
    const nTrails =
      Object.keys(overrides.trails.gap).length +
      Object.keys(overrides.trails.marks).length +
      Object.keys(overrides.trails.types).length;
    const nShields = Object.keys(overrides.shields).length;
    const perMap = Object.entries(overrides.maps)
      .map(([id, m]) => `${mapTypeDef(id).label} ${Object.keys(m.layers).length}`)
      .join(", ");
    status.textContent =
      (hasOverrides(overrides)
        ? `Zmeny: ${nPalette} farieb palety · ${nLayers} vrstiev (všetky mapy) · ` +
          `${nPoi} skrytých POI · ${nTrails} úprav trás · ` +
          `${nShields} štítkov · ` +
          `ikony ${selectedIconSource(overrides)}` +
          (perMap ? ` · po mapách: ${perMap}` : "") +
          " · uložené v prehliadači"
        : "Žiadne zmeny – mapa beží na pôvodnom štýle.") +
      // Čo sa pri kopírovaní štýlu neprenieslo, musí byť vidieť: polovičný
      // vklad bez slova vyzerá ako pokazené kopírovanie.
      (clipNote ? ` — ${clipNote}` : "");
  }

  // ---------- pomocníci nad overrides ----------
  // Úpravy sú v dvoch priečinkoch: spoločné (`overrides.layers`) a pre jeden
  // typ mapy (`overrides.maps[<typ>].layers`). Čítanie ich mieša rovnako ako
  // generátor štýlu, zápis ide do toho, ktorý je práve zvolený.

  /** Priečinok pre daný typ mapy; `create` ho v prípade potreby založí. */
  function mapBucket(create = false) {
    const id = mapTypeId();
    if (!overrides.maps[id] && create) {
      overrides.maps[id] = { layers: {}, poi: { hidden: [] } };
    }
    return overrides.maps[id];
  }

  /** Prázdne priečinky sa nemajú vláčiť do `style-overrides.json`. */
  function pruneMaps() {
    for (const [id, m] of Object.entries(overrides.maps)) {
      if (!Object.keys(m.layers || {}).length && !(m.poi?.hidden || []).length) {
        delete overrides.maps[id];
      }
    }
  }

  const baseLayerOverride = (id) => overrides.layers[id];
  const mapLayerOverride = (id) => mapBucket()?.layers?.[id];

  /** Úprava vrstvy tak, ako ju vidí mapa: spoločná a nad ňou tá pre túto mapu. */
  function layerOverride(id) {
    const base = baseLayerOverride(id);
    const own = mapLayerOverride(id);
    if (!base) return own;
    if (!own) return base;
    return {
      ...base,
      ...own,
      ...(base.paint || own.paint
        ? { paint: { ...(base.paint || {}), ...(own.paint || {}) } }
        : {})
    };
  }

  /** Úprava v tom priečinku, do ktorého sa práve zapisuje. */
  const scopedOverride = (id) =>
    editScope === "all" ? baseLayerOverride(id) : mapLayerOverride(id);

  function setLayerOverride(id, patch) {
    const bucket = editScope === "all" ? overrides.layers : mapBucket(true).layers;
    const cur = { ...(bucket[id] || {}) };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete cur[k];
      else cur[k] = v;
    }
    if (cur.paint && !Object.keys(cur.paint).length) delete cur.paint;
    if (Object.keys(cur).length) bucket[id] = cur;
    else delete bucket[id];
    pruneMaps();
  }

  function setLayerPaint(id, prop, value) {
    const cur = { ...((scopedOverride(id) || {}).paint || {}) };
    if (value === undefined) delete cur[prop];
    else cur[prop] = value;
    setLayerOverride(id, { paint: Object.keys(cur).length ? cur : undefined });
  }

  /**
   * Zmení jednu vlastnosť vzoru / okraja bez toho, aby zmazala ostatné.
   * Vychádza sa zo zlúčenej úpravy, takže doladenie vzoru v jednej mape
   * prevezme, čo je nastavené spoločne, a nezačne od nuly.
   */
  function patchSub(id, key, patch, base) {
    // `base` je to, čo má vrstva zabudované v štýle (napr. kamienky v skalnej
    // ploche). Bez neho by prvá zmena farby vzoru zahodila jeho veľkosť
    // a hrúbku – úprava by vznikla z prázdna, nie z toho, čo je vidieť.
    const cur = { ...(base || {}), ...((layerOverride(id) || {})[key] || {}) };
    setLayerOverride(id, { [key]: { ...cur, ...patch } });
  }

  /**
   * Zahodí VŠETKY úpravy jednej vrstvy – nie len farbu.
   *
   * Rozsah je ten istý, aký hovorí prepínač hore: „len táto mapa" vyčistí
   * priečinok tejto mapy, „všetky mapy" vyčistí spoločný AJ výnimky vo
   * všetkých typoch máp. To druhé je rovnaké pravidlo ako pri `setVisible`:
   * keď povieš „všetky", nesmie ostať mapa, v ktorej to platí inak.
   *
   * Farby z palety sa NEMAŽÚ – tie sú vlastnosťou témy, nie vrstvy (jednu
   * farbu zdieľa aj desať vrstiev) a majú vlastné `⟲` priamo pri sebe.
   */
  function resetLayer(id) {
    if (editScope === "all") {
      delete overrides.layers[id];
      for (const m of Object.values(overrides.maps)) delete m.layers?.[id];
    } else {
      delete mapBucket()?.layers?.[id];
    }
    pruneMaps();
  }

  /** Čo po resete v rozsahu „len táto mapa" na vrstve ešte ostane. */
  function resetLeftovers(id) {
    if (editScope === "all") return "";
    const base = baseLayerOverride(id);
    return base ? " Spoločná úprava (všetky mapy) ostane." : "";
  }

  /** Ľudské mená toho, čo sa vkladá – „paint" v stavovom riadku nikomu nepomôže. */
  const PATCH_LABELS = {
    dash: "prerušovanie čiary",
    pattern: "vzor",
    outline: "okraj",
    icon: "ikona"
  };

  /** Čo presne z odfoteného štýlu do vrstvy sadne, po ľudsky. */
  const patchText = (patch) =>
    Object.entries(patch)
      .flatMap(([key, value]) => (key === "paint" ? Object.keys(value) : [PATCH_LABELS[key] || key]))
      .join(", ");

  /** „do 1 vrstvy" / „do 3 vrstiev" – kmeň sa mení, lepiť príponu nestačí. */
  const vrstiev = (n) => (n === 1 ? "1 vrstvy" : `${n} vrstiev`);

  /** Odfotí vzhľad vrstvy do schránky panelu. */
  function copyStyle(layer) {
    styleClip = snapshotStyle(layer, layerOverride(layer.id) || {});
    clipNote = styleClip.dropped.length
      ? `Skopírované z „${styleClip.label}"; ` +
        `${styleClip.dropped.join(", ")} sa do úprav zapísať nedá ` +
        `(hodnota podľa atribútu prvku alebo vlastné prerušovanie) – ` +
        `to sa nekopíruje.`
      : `Skopírované z „${styleClip.label}".`;
  }

  /** Vloží schránku do vrstiev a povie, čo sa na ne nezmestilo. */
  function pasteStyleInto(ids) {
    if (!styleClip) return;
    const style = getStyle();
    const skipped = new Set();
    let done = 0;
    for (const id of ids) {
      const target = style.layers.find((l) => l.id === id);
      // Vrstva, z ktorej sa kopírovalo, sa preskočí: vznikla by z toho úprava
      // presne s tým, čo v štýle aj tak je – teda „zmena", ktorá nič nemení.
      if (!target || target.id === styleClip.from) continue;
      const { patch, skipped: miss } = pasteStyle(styleClip, target);
      for (const m of miss) skipped.add(m);
      if (!Object.keys(patch).length) continue;
      // `paint` sa dopĺňa po vlastnostiach, aby vloženie nezmazalo farbu,
      // ktorú si tam niekto nastavil zvlášť; ostatné kľúče sa prepíšu.
      const cur = { ...((scopedOverride(id) || {}).paint || {}) };
      setLayerOverride(id, {
        ...patch,
        ...(patch.paint ? { paint: { ...cur, ...patch.paint } } : {})
      });
      done += 1;
    }
    clipNote =
      `Vložený štýl z „${styleClip.label}" do ${vrstiev(done)}` +
      (skipped.size ? ` · neprenieslo sa: ${[...skipped].join(", ")}` : "");
  }

  /** Koľko úprav drží daný priečinok (do popisku prepínača rozsahu). */
  const countScope = (scope) =>
    scope === "all"
      ? Object.keys(overrides.layers).length + overrides.poi.hidden.length
      : Object.keys(mapBucket()?.layers || {}).length +
        (mapBucket()?.poi?.hidden || []).length;

  function setPaletteColor(key, value) {
    const theme = getTheme();
    const cur = { ...(overrides.palette[theme] || {}) };
    if (value === undefined || value.toLowerCase() === THEMES[theme][key]) delete cur[key];
    else cur[key] = value.toLowerCase();
    if (Object.keys(cur).length) overrides.palette[theme] = cur;
    else delete overrides.palette[theme];
  }

  /** Farebné vlastnosti vrstvy = všetko, čo v paint končí na `-color`. */
  const colorProps = (layer) =>
    Object.entries(layer.paint || {})
      .filter(([k, v]) => k.endsWith("-color") && typeof v === "string")
      .map(([k, v]) => [k, v]);

  const primaryColorProp = (layer) => {
    const props = colorProps(layer);
    const main = props.find(([k]) => !k.includes("halo") && !k.includes("outline"));
    return (main || props[0] || [null])[0];
  };

  const primaryColor = (layer) => {
    const prop = primaryColorProp(layer);
    return prop ? layer.paint[prop] : "#888888";
  };

  /** Podporuje vrstva vzor a okraj? (plochy a čiary áno, popisky nie) */
  const canDecorate = (layer) =>
    layer.type === "fill" || layer.type === "line" || layer.type === "fill-extrusion";

  // ---------- ovládacie prvky ----------
  function colorControl({ value, onInput, onReset, changed, note }) {
    const hex = toHex6(value);
    // Alfu drží políčko, nie pipetka – rozpis pri `alphaOf` vyššie.
    let alpha = alphaOf(value);
    const picker = el("input", { type: "color", class: "dev-color", value: hex });
    const text = el("input", {
      type: "text",
      class: "dev-hex",
      value: withAlpha(hex, alpha),
      spellcheck: "false",
      title: "#rrggbb, alebo #rrggbbaa aj s krytím"
    });
    picker.addEventListener("input", () => {
      text.value = withAlpha(picker.value, alpha);
      onInput(text.value);
    });
    text.addEventListener("change", () => {
      // Nezrozumiteľný zápis nemá prepísať farbu na čiernu: vrátime sa
      // k tomu, čo v políčku bolo, a alfa ostane, kde bola.
      if (!isHexColor(text.value)) {
        text.value = withAlpha(picker.value, alpha);
        return;
      }
      const v = normColor(text.value);
      alpha = alphaOf(v);
      text.value = v;
      picker.value = toHex6(v);
      onInput(v);
    });
    return el("div", { class: `dev-colorrow${changed ? " changed" : ""}` }, [
      picker,
      text,
      el("button", {
        type: "button",
        class: "dev-mini",
        title: "Kopírovať farbu",
        text: "⧉",
        onclick: (ev) => copyText(text.value, ev.currentTarget)
      }),
      onReset
        ? el("button", {
            type: "button",
            class: "dev-mini",
            title: "Späť na pôvodnú farbu",
            text: "⟲",
            onclick: onReset
          })
        : null,
      note ? el("span", { class: "dev-note", text: note }) : null
    ]);
  }

  /**
   * Číselné políčko.
   *
   * `stepFrom` je tá istá vec ako „auto" v placeholderi, len pre ŠÍPKY:
   * prázdne políčko typu `number` skočí šípkou dole na spodnú medzu, čiže
   * `line-width` na nulu – a vrstva ticho zmizne z mapy. Prvé stlačenie šípky
   * preto políčko naplní tým, čo tá vlastnosť práve v mape má, a ďalej sa už
   * kroky rátajú odtiaľ. (Natívne šípky myšou sa zachytiť nedajú, preto ich
   * `.dev-num` v `index.html` nemá vôbec – ostávajú klávesové, tie prejdú
   * cez tento handler.)
   */
  function numberField({ label, value, min, max, step, onChange, placeholder, stepFrom, title }) {
    const input = el("input", {
      type: "number",
      class: "dev-num",
      min,
      max,
      step,
      value: value ?? "",
      placeholder: placeholder || "",
      title: title || null
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
      if (input.value !== "" || stepFrom == null) return;
      ev.preventDefault();
      input.value = String(stepFrom);
      input.dispatchEvent(new Event("change"));
    });
    input.addEventListener("change", () =>
      onChange(input.value === "" ? undefined : Number(input.value))
    );
    return el("label", { class: "dev-field" }, [el("span", { text: label }), input]);
  }

  function selectField({ label, value, options, onChange }) {
    const select = el("select", { class: "dev-select" });
    for (const [val, text] of options) {
      const opt = new Option(text, val);
      opt.selected = val === value;
      select.add(opt);
    }
    select.addEventListener("change", () => onChange(select.value));
    return el("label", { class: "dev-field" }, [el("span", { text: label }), select]);
  }

  // ---------- tab: vrstvy ----------
  /** Vrstvy, ktoré vypisujeme – odvodené (vzor, okraj) patria pod svoju predlohu. */
  function listedLayers() {
    return getStyle().layers.filter((l) => !(l.metadata || {})["frico:derived"]);
  }

  function visibleLayers() {
    const q = search.trim().toLowerCase();
    return listedLayers().filter((l) => {
      const meta = l.metadata || {};
      const kind = meta["frico:kind"] || "line";
      if (kindFilter.size && !kindFilter.has(kind)) return false;
      if (onlyActive && !activeAt(l, zoomView)) return false;
      if (!q) return true;
      const hay = `${l.id} ${meta["frico:label"] || ""} ${l["source-layer"] || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }

  function isHidden(layer) {
    return isHiddenLayer(layer);
  }

  /**
   * Zapnutie a vypnutie vrstvy.
   *
   * Zapnutie nie je vždy len „prestaň ju vypínať": vrstvu môže vypínať profil
   * typu mapy (lyžiarske trasy na turistickej mape) alebo spoločná úprava –
   * vtedy treba do tejto mapy zapísať výslovné `visible: true`, inak by sa
   * odstránením úpravy nič nezmenilo.
   */
  function setVisible(ids, visible) {
    const byProfile = mapTypeHidden(getStyle()?.layers || [], mapTypeId());
    for (const id of ids) {
      // „Všetky mapy" musí znamenať naozaj všetky: výnimka nastavená
      // v niektorej mape by inak spoločné rozhodnutie potichu prebila.
      if (editScope === "all") clearMapVisibility(id);

      if (!visible) {
        setLayerOverride(id, { visible: false });
        continue;
      }
      const forcedOff =
        byProfile.has(id) ||
        (editScope === "map" && baseLayerOverride(id)?.visible === false);
      setLayerOverride(id, { visible: forcedOff ? true : undefined });
    }
  }

  /** Zahodí výnimku „zobraziť/skryť" tejto vrstvy vo všetkých typoch máp. */
  function clearMapVisibility(id) {
    for (const m of Object.values(overrides.maps)) {
      const own = m.layers?.[id];
      if (!own || own.visible === undefined) continue;
      delete own.visible;
      if (!Object.keys(own).length) delete m.layers[id];
    }
    pruneMaps();
  }

  /**
   * „Na tomto zoome áno / nie" – to hlavné, čo od prehliadania po zoomoch
   * človek chce. Rozsah vrstvy sa posunie tak, aby zoom `z` do neho patril
   * (alebo nepatril); keď by z rozsahu nič neostalo, vrstva sa rovno vypne.
   */
  function setZoomAt(layer, z, on) {
    const patch = zoomRangeFor(layer, z, on);
    if (patch.hide) {
      setVisible([layer.id], false);
      return;
    }
    if (patch.show && isHidden(layer)) setVisible([layer.id], true);
    const { minzoom, maxzoom } = patch;
    if ("minzoom" in patch || "maxzoom" in patch) {
      setLayerOverride(layer.id, {
        ...("minzoom" in patch ? { minzoom } : {}),
        ...("maxzoom" in patch ? { maxzoom } : {})
      });
    }
  }

  /** Prepínač, do ktorého priečinka idú úpravy (a čo je práve na obrazovke). */
  function scopeBar() {
    const type = mapTypeDef(mapTypeId());
    const chip = (id, label, title) =>
      el("button", {
        type: "button",
        class: `dev-chip${editScope === id ? " on" : ""}`,
        text: `${label} (${countScope(id)})`,
        title,
        onclick: () => {
          editScope = id;
          try {
            localStorage.setItem(SCOPE_KEY, id);
          } catch {
            /* súkromný režim – rozsah sa jednoducho nezapamätá */
          }
          renderBody();
        }
      });

    return el("div", { class: "dev-scope" }, [
      el("div", { class: "dev-scoperow" }, [
        el("span", { class: "dev-bulklabel", text: `Mapa: ${type.label}` }),
        chip("map", "len táto mapa", `Úpravy sa zapíšu len pre mapu „${type.label}".`),
        chip("all", "všetky mapy", "Úpravy sa zapíšu spoločne pre všetky typy máp.")
      ]),
      el("p", { class: "dev-note", text: type.note })
    ]);
  }

  /** Celé číslo zoomu, s ktorým sa pracuje pri „na tomto zoome áno/nie". */
  const zoomCell = () => Math.min(MAX_DISPLAY_Z, Math.max(0, Math.floor(zoomView)));

  function goToZoom(z) {
    zoomView = Math.min(MAX_DISPLAY_Z, Math.max(0, Number(z)));
    getMap()?.jumpTo({ zoom: zoomView });
    // Prekreslenie ide o tik neskôr, nie priamo tu. Volá sa to totiž
    // z `change` handlera číselného políčka a Chromium neznesie, keď sa
    // uprostred spracovania udalosti vymení podstrom, v ktorom ten input
    // leží: `replaceChildren` skončí výnimkou „The node to be removed is no
    // longer a child of this node". Tá istá obchádzka je v `apply()` a bola
    // tam napísaná presne z tohto dôvodu – tu chýbala.
    setTimeout(renderBody, 0);
  }

  /**
   * Posuvník zoomu + prehľad, koľko vrstiev je na ňom povolených.
   *
   * Zoom je tu hlavný nástroj, nie len informácia: nastav zoom a potom
   * jedným klikom (štítok s rozsahom v riadku, pásik v detaile, alebo
   * hromadne pre vybrané vrstvy) povedz, čo na ňom má a nemá byť.
   */
  function zoomBar() {
    const all = listedLayers();
    const active = all.filter((l) => activeAt(l, zoomView)).length;
    const z = zoomCell();

    const slider = el("input", {
      type: "range",
      class: "dev-slider",
      min: "0",
      max: String(MAX_DISPLAY_Z),
      step: "0.5",
      value: String(Math.round(zoomView * 2) / 2)
    });
    const number = el("input", {
      type: "number",
      class: "dev-num",
      min: "0",
      max: String(MAX_DISPLAY_Z),
      step: "0.5",
      value: String(Math.round(zoomView * 10) / 10)
    });

    slider.addEventListener("input", () => {
      number.value = slider.value;
    });
    slider.addEventListener("change", () => goToZoom(slider.value));
    number.addEventListener("change", () => goToZoom(number.value));

    // Skoky na zoomy, kde sa mapa naozaj láme (prehľad → okres → mesto →
    // ulica → detail), nech sa nemusí trafovať posuvníkom.
    const jumps = [4, 8, 10, 12, 14, 16, 18, 20].map((zz) =>
      el("button", {
        type: "button",
        class: `dev-chip${z === zz ? " on" : ""}`,
        text: `z${zz}`,
        onclick: () => goToZoom(zz)
      })
    );

    return el("div", { class: "dev-zoombar" }, [
      el("div", { class: "dev-zoomrow" }, [
        el("span", { class: "dev-zoomlabel", text: `Zoom ${zoomView.toFixed(1)}` }),
        slider,
        number
      ]),
      el("div", { class: "dev-chips" }, jumps),
      el("div", { class: "dev-zoominfo" }, [
        el("span", {
          text: `Na z${z} kreslí mapa ${active} zo ${all.length} vrstiev.`
        }),
        el("button", {
          type: "button",
          class: `dev-chip${onlyActive ? " on" : ""}`,
          text: "len aktívne",
          onclick: () => {
            onlyActive = !onlyActive;
            renderBody();
          }
        })
      ]),
      el("p", {
        class: "dev-note",
        text:
          `Štítok s rozsahom v riadku (napr. z13–16) je prepínač: klik povie, ` +
          `či sa vrstva na z${z} kresliť má, alebo nie. Celý rozsah sa dá ` +
          `naklikať v pásiku v detaile vrstvy.`
      })
    ]);
  }

  /**
   * Pásik zoomov z0–z20: jedna bunka = jeden zoom, zvýraznené sú tie, na
   * ktorých sa vrstva kreslí. Klik do bunky ju zapne alebo vypne – rozsah
   * ostáva súvislý, takže z pásika je rovno vidieť, čo vrstva robí.
   */
  function zoomStrip(layer) {
    const now = zoomCell();
    const cells = [];
    for (let z = 0; z <= MAX_DISPLAY_Z; z++) {
      const on = activeAt(layer, z);
      cells.push(
        el("button", {
          type: "button",
          class: `dev-cell${on ? " on" : ""}${z === now ? " now" : ""}`,
          title: `z${z}: ${on ? "kreslí sa – klik vypne" : "nekreslí sa – klik zapne"}`,
          onclick: () => {
            setZoomAt(layer, z, !on);
            apply({ immediate: true });
          }
        })
      );
    }
    return el("div", { class: "dev-stripwrap" }, [
      el("div", { class: "dev-strip" }, cells),
      el("div", { class: "dev-striplabels" }, [
        el("span", { text: "z0" }),
        el("span", { text: "z5" }),
        el("span", { text: "z10" }),
        el("span", { text: "z15" }),
        el("span", { text: "z20" })
      ])
    ]);
  }

  function renderLayers() {
    const layers = visibleLayers();

    const searchInput = el("input", {
      type: "search",
      class: "dev-search",
      placeholder: "Hľadať vrstvu (napr. les, road-motorway…)",
      value: search
    });
    searchInput.addEventListener("input", () => {
      search = searchInput.value;
      renderBody();
      const next = body.querySelector(".dev-search");
      if (next) {
        next.focus();
        next.setSelectionRange(next.value.length, next.value.length);
      }
    });

    const chips = el("div", { class: "dev-chips" }, [
      el("button", {
        type: "button",
        class: `dev-chip${kindFilter.size ? "" : " on"}`,
        text: "Všetko",
        onclick: () => {
          kindFilter.clear();
          renderBody();
        }
      }),
      ...LAYER_KINDS.map((k) =>
        el("button", {
          type: "button",
          class: `dev-chip${kindFilter.has(k.id) ? " on" : ""}`,
          text: k.label,
          onclick: () => {
            if (kindFilter.has(k.id)) kindFilter.delete(k.id);
            else kindFilter.add(k.id);
            renderBody();
          }
        })
      )
    ]);

    // ----- hromadné operácie -----
    const bulkColor = el("input", { type: "color", class: "dev-color", value: "#ff0000" });
    const bulk = el("div", { class: `dev-bulk${selectedLayers.size ? " on" : ""}` }, [
      el("span", { class: "dev-bulklabel", text: `Vybraných: ${selectedLayers.size}` }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Vybrať zobrazené",
        onclick: () => {
          for (const l of visibleLayers()) selectedLayers.add(l.id);
          renderBody();
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Zrušiť výber",
        onclick: () => {
          selectedLayers.clear();
          renderBody();
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Zobraziť",
        onclick: () => {
          setVisible(selectedLayers, true);
          apply();
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Skryť",
        onclick: () => {
          setVisible(selectedLayers, false);
          apply();
        }
      }),
      // Hromadné „na tomto zoome áno/nie": vyber vrstvy, nastav zoom a jedným
      // tlačidlom povedz, čo na ňom má byť vidieť.
      el("button", {
        type: "button",
        class: "dev-btn",
        text: `Zobraziť od z${zoomCell()}`,
        title: `Vybraným vrstvám nastaví minzoom na ${zoomCell()}`,
        onclick: () => {
          for (const id of selectedLayers) {
            setLayerOverride(id, { minzoom: zoomCell() });
          }
          setVisible(selectedLayers, true);
          apply();
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: `Skryť na z${zoomCell()}`,
        title: `Vybrané vrstvy sa na z${zoomCell()} kresliť nebudú`,
        onclick: () => {
          const style = getStyle();
          for (const id of selectedLayers) {
            const layer = style.layers.find((l) => l.id === id);
            if (layer) setZoomAt(layer, zoomCell(), false);
          }
          apply();
        }
      }),
      bulkColor,
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Zafarbiť výber",
        onclick: () => {
          const style = getStyle();
          for (const id of selectedLayers) {
            const layer = style.layers.find((l) => l.id === id);
            const prop = layer && primaryColorProp(layer);
            if (prop) setLayerPaint(id, prop, bulkColor.value);
          }
          apply();
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Kopírovať farby",
        onclick: (ev) => {
          const style = getStyle();
          const out = {};
          for (const id of selectedLayers) {
            const layer = style.layers.find((l) => l.id === id);
            if (layer) out[id] = Object.fromEntries(colorProps(layer));
          }
          copyText(JSON.stringify(out, null, 2), ev.currentTarget);
        }
      }),
      // Schránka na štýl je z detailu vrstvy, ale vložiť sa dá aj hromadne –
      // „nech všetky múry vyzerajú ako plot" je jedno zadanie, nie desať.
      styleClip
        ? el("button", {
            type: "button",
            class: "dev-btn",
            text: `⤵ Vložiť štýl z „${styleClip.label}"`,
            title: "Vloží skopírovaný štýl do všetkých vybraných vrstiev; "
              + "čo sa na ne nezmestí, povie stavový riadok",
            onclick: () => {
              pasteStyleInto([...selectedLayers]);
              apply();
            }
          })
        : null,
      el("button", {
        type: "button",
        class: "dev-btn danger",
        text: editScope === "all" ? "Reset (všetky mapy)" : "Reset (táto mapa)",
        title: "Zahodí úpravy vybraných vrstiev v tom priečinku, do ktorého sa práve zapisuje",
        onclick: () => {
          for (const id of selectedLayers) resetLayer(id);
          apply();
        }
      })
    ]);

    // ----- zoznam po skupinách -----
    const byGroup = new Map();
    for (const layer of layers) {
      const g = (layer.metadata || {})["frico:group"] || "ostatne";
      if (!byGroup.has(g)) byGroup.set(g, []);
      byGroup.get(g).push(layer);
    }
    const order = [...LAYER_GROUPS.map((g) => g.id), ...byGroup.keys()];
    const seen = new Set();
    const groups = [];

    for (const gid of order) {
      if (seen.has(gid) || !byGroup.has(gid)) continue;
      seen.add(gid);
      const list = byGroup.get(gid);
      const ids = list.map((l) => l.id);
      const nHidden = list.filter(isHidden).length;
      const nActive = list.filter((l) => activeAt(l, zoomView)).length;
      const open = !collapsed.has(gid);

      groups.push(
        el("div", { class: "dev-group" }, [
          el("button", {
            type: "button",
            class: "dev-groupname",
            text: `${open ? "▾" : "▸"} ${GROUP_LABELS[gid] || gid} (${nActive}/${list.length})`,
            title: `Na zoome ${zoomView.toFixed(1)} je aktívnych ${nActive} z ${list.length}`,
            onclick: () => {
              if (open) collapsed.add(gid);
              else collapsed.delete(gid);
              renderBody();
            }
          }),
          el("button", {
            type: "button",
            class: "dev-mini",
            title: nHidden === list.length ? "Zobraziť celú skupinu" : "Skryť celú skupinu",
            text: nHidden === list.length ? "🚫" : "👁",
            onclick: () => {
              setVisible(ids, nHidden === list.length);
              apply();
            }
          }),
          el("button", {
            type: "button",
            class: "dev-mini",
            title: "Vybrať skupinu",
            text: "☑",
            onclick: () => {
              const allSelected = ids.every((id) => selectedLayers.has(id));
              for (const id of ids) {
                if (allSelected) selectedLayers.delete(id);
                else selectedLayers.add(id);
              }
              renderBody();
            }
          })
        ])
      );

      if (open) for (const layer of list) groups.push(layerRow(layer));
    }

    return el("div", {}, [
      scopeBar(),
      zoomBar(),
      searchInput,
      chips,
      bulk,
      el("div", { class: "dev-list" }, groups)
    ]);
  }

  function layerRow(layer) {
    const meta = layer.metadata || {};
    const kind = meta["frico:kind"] || "line";
    const o = layerOverride(layer.id);
    const hidden = isHidden(layer);
    const open = expanded.has(layer.id);
    const inactive = !activeAt(layer, zoomView);

    const check = el("input", { type: "checkbox", class: "dev-check" });
    check.checked = selectedLayers.has(layer.id);
    check.addEventListener("change", () => {
      if (check.checked) selectedLayers.add(layer.id);
      else selectedLayers.delete(layer.id);
      const label = body.querySelector(".dev-bulklabel");
      if (label) label.textContent = `Vybraných: ${selectedLayers.size}`;
    });

    // Riadok rozlišuje „mám to upravené v tejto mape" od „mám to upravené
    // spoločne" – inak by sa nedalo poznať, kde úprava vlastne sedí.
    const scoped = scopedOverride(layer.id);
    const cls =
      `dev-row${o ? " changed" : ""}${scoped ? " scoped" : ""}` +
      `${hidden ? " off" : ""}${inactive && !hidden ? " inactive" : ""}`;
    const head = el("div", { class: cls }, [
      check,
      el("button", {
        type: "button",
        class: "dev-mini",
        title: hidden
          ? `Zobraziť vrstvu (${editScope === "all" ? "vo všetkých mapách" : "v tejto mape"})`
          : `Skryť vrstvu (${editScope === "all" ? "vo všetkých mapách" : "v tejto mape"})`,
        text: hidden ? "🚫" : "👁",
        onclick: () => {
          setVisible([layer.id], hidden);
          apply();
        }
      }),
      el("span", { class: `dev-kind k-${kind}`, text: KIND_LABELS[kind] || kind }),
      el("button", {
        type: "button",
        class: "dev-name",
        title: `${layer.id} – klikni pre detaily`,
        onclick: () => {
          if (open) expanded.delete(layer.id);
          else expanded.add(layer.id);
          renderBody();
        }
      }, [
        el("span", { text: `${open ? "▾ " : "▸ "}${meta["frico:label"] || layer.id}` }),
        el("small", { text: layer.id })
      ]),
      // Štítok s rozsahom je zároveň prepínač „na tomto zoome áno / nie" –
      // to je pri prezeraní po zoomoch tá najčastejšia úprava vôbec.
      el("button", {
        type: "button",
        class: `dev-zrange${inactive ? " off" : ""}`,
        text: zoomRangeText(layer),
        title: inactive
          ? `Na z${zoomCell()} sa nekreslí – klikni, nech sa kreslí`
          : `Na z${zoomCell()} sa kreslí – klikni, nech sa nekreslí`,
        onclick: () => {
          setZoomAt(layer, zoomCell(), inactive);
          apply({ immediate: true });
        }
      })
    ]);

    return el("div", { class: "dev-item" }, [head, open ? layerDetails(layer) : null]);
  }

  /** Nadpis sekcie v detaile vrstvy – aby bolo vidieť, čo sa kde nastavuje. */
  const sectionTitle = (text, note) =>
    el("div", { class: "dev-h5" }, [
      el("span", { text }),
      note ? el("small", { text: note }) : null
    ]);

  /**
   * Výber prerušovania čiary s náhľadom. Samotný názov („Čiarkovaná") o čiare
   * veľa nepovie – vedľa výberu sa preto rovno kreslí, ako bude vyzerať.
   */
  function dashField({ label, value, onChange }) {
    const preview = el("span", { class: "dev-dash" });
    const draw = (id) => {
      const d = dashPreview(id, 2);
      preview.innerHTML =
        `<svg width="54" height="10" viewBox="0 0 54 10" aria-hidden="true">` +
        `<line x1="1" y1="5" x2="53" y2="5" stroke="currentColor" stroke-width="2"` +
        (d ? ` stroke-dasharray="${d}"` : "") +
        `/></svg>`;
    };
    draw(value);
    const field = selectField({
      label,
      value,
      options: DASH_PRESETS.map((d) => [d.id, d.label]),
      onChange: (v) => {
        draw(v);
        onChange(v);
      }
    });
    return el("span", { class: "dev-dashrow" }, [field, preview]);
  }

  /**
   * Číselná vlastnosť z `paint` (šírka čiary, krytie). Väčšina z nich je
   * v štýle zadaná interpoláciou podľa zoomu – vtedy je pole prázdne
   * a v ňom nápoveda; vyplnením sa nahradí konštantou, vymazaním sa vráti
   * pôvodná interpolácia.
   */
  /**
   * Editor HODNOTY PODĽA ZOOMU – „na z11 takto, na z16 inak".
   *
   * DVA TVARY, DVE OTÁZKY, JEDEN PREPÍNAČ:
   *
   *   krivka  `[[zoom, hodnota], …]`      … ako hodnota RASTIE (plynulý prechod)
   *   pásma   `[[od, do, hodnota], …]`    … čo PLATÍ v tomto rozsahu (skok na hranici)
   *
   * PREČO PRIBUDLI PÁSMA. Kým bola k dispozícii len krivka, „od z9 do z11
   * takáto čiara, na z12 takáto, od z13 do z17 takáto" sa muselo napísať
   * šiestimi zlomami – tú istú hodnotu dvakrát, na oboch hranicách každého
   * pásma –, inak sa medzi nimi plynule menila. Pri strope
   * {@link MAX_PAINT_STOPS} zlomov sa do toho zmestili štyri pásma a piate
   * už nie. V pásmach je to jeden riadok na pásmo.
   *
   * PREČO SA `do` NEDÁ PÍSAŤ VOĽNE. Pásma musia ísť za sebou bez medzier
   * a bez prekryvov (inak ich `normalizeOverrides` odmietne a s nimi celú
   * vlastnosť), takže rozhoduje HRANICA: `do` jedného pásma je vždy `od`
   * nasledujúceho mínus jedna. Editovateľné je preto `od` každého pásma
   * (posúva hranicu) a `do` toho POSLEDNÉHO; ostatné `do` sa dopočítajú.
   * Z tohto panela sa tak medzera nedá vyrobiť ani omylom.
   *
   * PREČO PODĽA AKTUÁLNEHO ZOOMU. Zlom aj hranica pásma sa pridáva tam, kde
   * práve stojí mapa (`+ zlom pri z14`, `+ pásmo od z14`), lebo tak sa mapa
   * aj ladí: nastavíš zoom, pozrieš sa, opravíš farbu. Písať zoom do políčka
   * a až potom sa naň presunúť by bolo to isté dvakrát – a druhý raz naslepo.
   *
   * Jeden zlom aj jedno pásmo sú platné a znamenajú pevnú hodnotu
   * (`interpolate` ani `step` s jediným výstupom by nemal čo robiť, preto ich
   * `paintValue` rozbalí).
   */
  function zoomStopsEditor({ layer, prop, kind, min, max, step }) {
    const o = layerOverride(layer.id) || {};
    const cur = o.paint && o.paint[prop];
    const list = Array.isArray(cur) ? cur : null;
    const pasma = list ? isBandList(list) : false;
    const stops = list && !pasma ? list : null;
    const bands = pasma ? sortBands(list) : null;
    const zNow = zoomCell();

    const zapis = (next) => {
      // ZORADIŤ HNEĎ PRI ZÁPISE, nie až pri skladaní štýlu. Zlomy aj pásma
      // vznikajú v poradí, v akom ich naklikáš (najprv z18, potom z12),
      // a `interpolate` chce striktne rastúce – MapLibre by pri porušení
      // odmietol CELÝ štýl a mapa by sa nenačítala. Uložené má byť to isté,
      // čo je platné. Prázdny zoznam neukladáme – to je „nechaj, čo je v štýle".
      setLayerPaint(layer.id, prop, next && next.length ? next : undefined);
      apply({ immediate: true });
    };
    const setStops = (next) => zapis(next && next.length ? sortStops(next) : null);
    // Pásma sa pred zápisom EŠTE ZOSÚVISLIA: `do` každého okrem posledného je
    // `od` nasledujúceho mínus jedna. Vstup z políčka `od` sa tým môže dotknúť
    // dvoch riadkov naraz a nikdy z toho nevyjde medzera ani prekryv.
    const setBands = (next) => {
      if (!next || !next.length) return zapis(null);
      const zoradene = sortBands(next).filter(
        ([od], i, a) => i === 0 || od > a[i - 1][0]
      );
      zapis(
        zoradene.map(([od, doZ, v], i) => [
          od,
          i + 1 < zoradene.length ? zoradene[i + 1][0] - 1 : Math.max(od, doZ),
          v
        ])
      );
    };

    // Hodnota, s ktorou nový zlom (pásmo) začína: čo tá vlastnosť práve
    // v mape má.
    const nowValue = () => {
      const v = (layer.paint || {})[prop];
      const naZoome = valueAtZoom(list || v, zNow);
      if (naZoome != null) return naZoome;
      if (typeof v === "number" || (typeof v === "string" && v.startsWith("#"))) return v;
      return kind === "color" ? primaryColor(layer) : min ?? 1;
    };

    const hodnotaCtrl = (v, setVal) =>
      kind === "color"
        ? colorControl({ value: v, onInput: setVal, onReset: null, changed: false })
        : numberField({
            label: "", value: v, min, max, step, stepFrom: v,
            // Vyprázdnené políčko zlomu je spodná medza, NIE nula: nulová
            // hrúbka je neviditeľná čiara (viď `paintNumber`).
            onChange: (nv) => setVal(nv ?? min ?? 0)
          });

    const zoomInput = (z, onChange, title) => {
      const input = el("input", {
        type: "number", class: "dev-num dev-num-z",
        min: "0", max: String(MAX_DISPLAY_Z), step: "1", value: String(z),
        title: title || ""
      });
      input.addEventListener("change", () => onChange(Number(input.value)));
      return input;
    };

    // ---- riadky: krivka ----
    const stopRows = (stops || []).map(([z, v], i) => {
      const setVal = (nv) => setStops(stops.map((s, j) => (j === i ? [s[0], nv] : s)));
      return el("div", { class: "dev-stop" }, [
        el("span", { class: "dev-stopz", text: "z" }),
        zoomInput(z, (nz) => setStops(stops.map((s, j) => (j === i ? [nz, s[1]] : s)))),
        hodnotaCtrl(v, setVal),
        el("button", {
          type: "button", class: "dev-mini", title: "Zmazať tento zlom", text: "×",
          onclick: () => setStops(stops.filter((_, j) => j !== i))
        })
      ]);
    });

    // ---- riadky: pásma ----
    const bandRows = (bands || []).map(([od, doZ, v], i) => {
      const posledne = i + 1 === bands.length;
      const setVal = (nv) => setBands(bands.map((b, j) => (j === i ? [b[0], b[1], nv] : b)));
      // Hranica sa smie posunúť len tam, kde ostane pásmo aj susedovi –
      // inak by z posunu vypadol prázdny rozsah.
      const dolna = i === 0 ? 0 : bands[i - 1][0] + 1;
      const horna = posledne ? doZ : bands[i + 1][0] - 1;
      return el("div", { class: "dev-stop" }, [
        el("span", { class: "dev-stopz", text: "z" }),
        zoomInput(
          od,
          (nz) =>
            setBands(
              bands.map((b, j) =>
                j === i ? [Math.min(Math.max(nz, dolna), horna), b[1], b[2]] : b
              )
            ),
          `Od tohto zoomu platí táto hodnota (${dolna}–${horna})`
        ),
        posledne
          ? el("span", { class: "dev-stopz", text: "–" })
          : el("span", { class: "dev-stopz dev-bandto", text: `– ${doZ}` }),
        posledne
          ? zoomInput(
              doZ,
              (nz) => setBands(bands.map((b, j) => (j === i ? [b[0], Math.max(nz, b[0]), b[2]] : b))),
              "Po tento zoom vrátane; vyššie platí tá istá hodnota"
            )
          : null,
        hodnotaCtrl(v, setVal),
        el("button", {
          type: "button",
          class: "dev-mini",
          title: bands.length === 1
            ? "Zrušiť pásma"
            : "Zmazať toto pásmo – pohltí ho susedné, aby nevznikla medzera",
          text: "×",
          onclick: () => {
            if (bands.length === 1) return setBands(null);
            // Vypustené pásmo POHLTÍ sused: predošlé si predĺži `do`
            // (o to sa postará zosúvislenie v `setBands`), a keď je vypustené
            // prvé, druhé si posunie `od` na jeho začiatok.
            const zvysok = bands.filter((_, j) => j !== i);
            if (i === 0) zvysok[0] = [od, zvysok[0][1], zvysok[0][2]];
            setBands(zvysok);
          }
        })
      ]);
    });

    // ---- prepínač tvaru ----
    // Prepnutie NEZAHODÍ, čo je nastavené: krivka sa preloží na pásma (zlom
    // = začiatok pásma) a pásma na krivku (začiatok pásma = zlom). Presné to
    // byť nemôže – plynulý prechod a skok sú dve rôzne veci –, ale zahodiť
    // odladené hodnoty a nechať prázdno by bolo horšie.
    const prepni = (naPasma) => {
      if (naPasma) {
        const z = stops
          ? sortStops(stops).map(([sz, v], i, a) => [
              sz, i + 1 < a.length ? a[i + 1][0] - 1 : Math.max(sz, MAX_DISPLAY_Z), v
            ])
          : [[zNow, MAX_DISPLAY_Z, nowValue()]];
        setBands(z);
      } else {
        setStops(bands ? bands.map(([od, , v]) => [od, v]) : [[zNow, nowValue()]]);
      }
    };
    const prepinac = el("div", { class: "dev-shape" }, [
      el("button", {
        type: "button",
        class: `dev-mini dev-shapebtn${pasma ? "" : " on"}`,
        title: "Plynulý prechod medzi zlomami (interpolate)",
        text: "krivka",
        onclick: () => (pasma ? prepni(false) : null)
      }),
      el("button", {
        type: "button",
        class: `dev-mini dev-shapebtn${pasma ? " on" : ""}`,
        title: "V pásme konštantná hodnota, na hranici skok (step)",
        text: "pásma",
        onclick: () => (pasma ? null : prepni(true))
      })
    ]);

    const uzTam = pasma
      ? (bands || []).some(([od]) => od === zNow)
      : (stops || []).some(([z]) => z === zNow);
    const plno = (list || []).length >= MAX_PAINT_STOPS;
    return el("div", { class: "dev-stops" }, [
      list ? prepinac : null,
      ...(pasma ? bandRows : stopRows),
      el("button", {
        type: "button",
        class: "dev-mini dev-addstop",
        title: plno
          ? `Viac než ${MAX_PAINT_STOPS} ${pasma ? "pásiem" : "zlomov"} sa do úprav nezmestí`
          : uzTam
            ? `Na zoome ${zNow} už ${pasma ? "pásmo začína" : "zlom je"} – zmeň ho v riadku vyššie`
            : pasma
              ? `Rozdelí pásmo na zoome, kde práve stojí mapa`
              : `Pridá zlom na zoome, kde práve stojí mapa`,
        text: pasma ? `+ pásmo od z${zNow}` : `+ zlom pri z${zNow}`,
        disabled: uzTam || plno || null,
        onclick: () =>
          pasma
            // Nové pásmo vznikne ROZDELENÍM toho, v ktorom mapa práve stojí –
            // preto sa z tohto tlačidla nedá vyrobiť medzera.
            ? setBands([...bands, [zNow, MAX_DISPLAY_Z, nowValue()]])
            : setStops([...(stops || []), [zNow, nowValue()]])
      }),
      // Bez zoznamu je prepínač na konci: prvé ťuknutie zároveň hovorí, ktorý
      // tvar chceš, a hneď v ňom vyrobí prvý riadok.
      list
        ? el("button", {
            type: "button", class: "dev-mini",
            title: `Zrušiť ${pasma ? "pásma" : "zoomové zlomy"} a nechať, čo je v štýle`,
            text: "⟲",
            onclick: () => zapis(null)
          })
        : el("button", {
            type: "button", class: "dev-mini dev-addstop",
            title: "V pásme je hodnota konštantná a na hranici skočí (step)",
            text: `+ pásmo od z${zNow}`,
            onclick: () => setBands([[zNow, MAX_DISPLAY_Z, nowValue()]])
          })
    ]);
  }

  /**
   * Číselná vlastnosť z `paint` – a hlavne CESTA SPÄŤ NA „AUTO".
   *
   * Bez nej sa dala vlastnosť len nastaviť: hrúbka čiary je v štýle krivka
   * podľa zoomu, políčko preto ukazuje „auto" a jediný spôsob, ako sa k nemu
   * vrátiť, bolo vymazať políčko naslepo. Šípkou dole sa pritom prázdne
   * políčko skočí na spodnú medzu (nulová hrúbka = neviditeľná čiara), takže
   * z jedného ťuknutia bola zmiznutá vrstva bez cesty naspäť. Odteraz je pri
   * zmenenej hodnote `⟲` ako všade inde v paneli a v bublinke je aj to, čo
   * „auto" na tomto zoome znamená – aby bolo z čoho vyjsť.
   */
  function paintNumber({ layer, prop, label, min, max, step }) {
    const o = layerOverride(layer.id) || {};
    const cur = (layer.paint || {})[prop];
    const isNum = typeof cur === "number";
    const overridden = !!(o.paint && o.paint[prop] !== undefined);
    const back = () => {
      setLayerPaint(layer.id, prop, undefined);
      apply({ immediate: true });
    };
    const resetBtn = (title) =>
      el("button", { type: "button", class: "dev-mini", title, text: "⟲", onclick: back });

    // Keď tú istú vlastnosť riadia zoomové zlomy, pevná hodnota by ich prepísala
    // – políčko sa preto zamkne a povie, kde sa to teraz nastavuje. Dve páky na
    // jednu vlastnosť by sa inak tichým prepisom navzájom rušili.
    if (Array.isArray(o.paint && o.paint[prop])) {
      // Do políčka širokého 46 px sa „podľa zoomu" nezmestí (odsekne sa na
      // „podľa"), takže tam je pomlčka a vysvetlenie v bublinke – vedľajší
      // riadok „… podľa zoomu" povie zvyšok.
      const field = numberField({ label, value: undefined, min, max, step,
                                  placeholder: "—", onChange: () => {} });
      const input = field.querySelector("input");
      input.disabled = true;
      input.title = `${prop} je nastavené podľa zoomu nižšie (krivka alebo pásma) – `
        + `zruš to (⟲), ak chceš jednu pevnú hodnotu`;
      field.classList.add("changed");
      return field;
    }

    // Čo tá vlastnosť na TOMTO zoome naozaj robí. Bez toho je „auto" prázdne
    // miesto a prvá vlastná hodnota je hádanie.
    const auto = overridden ? null : valueAtZoom(cur, zoomView);
    const autoText = typeof auto === "number" ? String(auto) : null;
    const field = numberField({
      label,
      value: isNum ? Math.round(cur * 100) / 100 : undefined,
      min,
      max,
      step,
      // Do políčka sa „podľa zoomu" nezmestí, patrí teda do bublinky.
      placeholder: isNum ? "" : "auto",
      // Šípka na prázdnom políčku nesmie skočiť na spodnú medzu (0 = zmiznutá
      // čiara) – začne sa od toho, čo je v mape teraz.
      stepFrom: autoText,
      title: isNum
        ? `${prop} je pevná hodnota – ⟲ vedľa ju vráti na to, čo počíta štýl`
        : `${prop} sa v štýle mení podľa zoomu` +
          (autoText ? ` (na z${zoomCell()} je to ${autoText})` : "") +
          " – vyplnením sa nahradí pevnou hodnotou",
      onChange: (v) => {
        setLayerPaint(layer.id, prop, v);
        apply({ immediate: true });
      }
    });
    if (!overridden) return field;
    field.classList.add("changed");
    field.appendChild(resetBtn(`Späť na to, čo počíta štýl (${prop} podľa zoomu)`));
    return field;
  }

  /**
   * Panel nad detailom vrstvy: schránka na štýl a reset.
   *
   * Sú tu spolu naschvál – oboje je o CELEJ vrstve, nie o jednej vlastnosti,
   * a oboje dovtedy chýbalo: vrátiť sa dala len farba (každá zvlášť), takže
   * „daj to naspäť, ako to bolo" znamenalo prejsť všetky sekcie po jednej,
   * a „nech to vyzerá ako tamto" sa nedalo povedať vôbec.
   */
  function layerTools(layer) {
    const o = layerOverride(layer.id);
    const scoped = scopedOverride(layer.id);
    const kam = editScope === "all" ? "všetkých mapách" : `mape „${mapTypeDef(mapTypeId()).label}"`;
    const row = [
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "⧉ Skopírovať štýl",
        title: "Odfotí, ako táto vrstva vyzerá (farby, hrúbky, prerušovanie, "
          + "vzor, okraj), a ponúkne to na vloženie do inej vrstvy",
        onclick: () => {
          copyStyle(layer);
          renderBody();
          renderStatus();
        }
      })
    ];

    if (styleClip && styleClip.from !== layer.id) {
      const { patch, skipped } = pasteStyle(styleClip, layer);
      const moze = Object.keys(patch).length > 0;
      row.push(
        el("button", {
          type: "button",
          class: "dev-btn",
          text: `⤵ Vložiť štýl z „${styleClip.label}"`,
          disabled: moze ? null : true,
          title: moze
            ? `Vloží ${patchText(patch)} do tejto vrstvy (v ${kam})`
              + (skipped.length ? ` · neprenesie sa: ${skipped.join(", ")}` : "")
            : `Z „${styleClip.label}" (${styleClip.type}) sa na vrstvu typu `
              + `${layer.type} nedá preniesť nič`,
          onclick: () => {
            pasteStyleInto([layer.id]);
            apply({ immediate: true });
          }
        })
      );
    }

    if (o) {
      // Rozsah rozhoduje aj o tom, či je čo zahodiť: v „len táto mapa" sa
      // spoločná úprava zrušiť NEDÁ a tlačidlo, ktoré po stlačení nič
      // neurobí, je horšie než zhasnuté – povie prečo.
      const mozeReset = editScope === "all" || !!scoped;
      row.push(
        el("button", {
          type: "button",
          class: "dev-btn danger",
          text: editScope === "all" ? "⟲ Reset (všetky mapy)" : "⟲ Reset (táto mapa)",
          disabled: mozeReset ? null : true,
          title: mozeReset
            ? `Zahodí VŠETKY úpravy tejto vrstvy v ${kam} – viditeľnosť, `
              + `zoomy, farby, hrúbky, ikonu, vzor aj okraj.${resetLeftovers(layer.id)} `
              + `Farby z palety ostávajú, tie patria téme.`
            : "Úprava tejto vrstvy je spoločná pre všetky mapy – zrušiť sa dá "
              + `len v rozsahu „všetky mapy" (prepínač hore).`,
          onclick: () => {
            resetLayer(layer.id);
            apply({ immediate: true });
          }
        })
      );
    }

    return el("div", { class: "dev-tools" }, row);
  }

  function layerDetails(layer) {
    const o = layerOverride(layer.id) || {};
    const paletteMap = (layer.metadata || {})["frico:palette"] || {};
    const parts = [layerTools(layer)];

    // ---- rozsah zoomu ----
    // Zoom je prvý, lebo „čo je vidieť kedy" je najčastejšia otázka: pásik
    // ukáže celý rozsah naraz a klikom sa mení, čísla sú na jemné doladenie.
    const zoomField = (prop, label) =>
      numberField({
        label,
        value: layer[prop],
        min: 0,
        max: 24,
        step: 0.5,
        placeholder: prop === "minzoom" ? "0" : "24",
        // Šípka na prázdnom políčku by inak skočila na spodnú medzu, čiže
        // „do z0" – teda rozsah, v ktorom vrstva nie je nikde.
        stepFrom: prop === "minzoom" ? 0 : MAX_DISPLAY_Z,
        onChange: (v) => {
          setLayerOverride(layer.id, { [prop]: v });
          apply({ immediate: true });
        }
      });
    parts.push(sectionTitle("Zoom", "klik do pásika = na tom zoome áno / nie"));
    parts.push(zoomStrip(layer));
    parts.push(
      el("div", { class: `dev-fields${o.minzoom != null || o.maxzoom != null ? " changed" : ""}` }, [
        zoomField("minzoom", "od z"),
        zoomField("maxzoom", "do z"),
        el("button", {
          type: "button",
          class: "dev-btn",
          text: `od z${zoomCell()}`,
          title: `Vrstva sa začne kresliť až od z${zoomCell()}`,
          onclick: () => {
            setLayerOverride(layer.id, { minzoom: zoomCell() });
            apply({ immediate: true });
          }
        }),
        el("button", {
          type: "button",
          class: "dev-btn",
          text: `do z${zoomCell()}`,
          title: `Vrstva sa nad z${zoomCell()} kresliť prestane`,
          onclick: () => {
            setLayerOverride(layer.id, { maxzoom: zoomCell() });
            apply({ immediate: true });
          }
        }),
        o.minzoom != null || o.maxzoom != null
          ? el("button", {
              type: "button",
              class: "dev-mini",
              title: "Späť na pôvodný rozsah",
              text: "⟲",
              onclick: () => {
                setLayerOverride(layer.id, { minzoom: undefined, maxzoom: undefined });
                apply({ immediate: true });
              }
            })
          : null
      ])
    );

    // ---- farby ----
    if (colorProps(layer).length) parts.push(sectionTitle("Farby"));
    for (const [prop, value] of colorProps(layer)) {
      const paletteKey = paletteMap[prop];
      const overridden = !!(o.paint && o.paint[prop] !== undefined);
      const bezVyplne = o.paint && o.paint[prop] === NO_FILL;
      // BEZ VÝPLNE má zmysel len na ploche: plocha stratí farbu pozadia, ale
      // vzor aj okraj na nej ostanú. Nie je to krytie 0 (to zhasne aj obrys
      // z `fill-outline-color`) ani vypnutie vrstvy (tým by zmizol aj vzor).
      const canNoFill = prop === "fill-color" || prop === "fill-extrusion-color";
      parts.push(
        el("div", { class: "dev-prop" }, [
          el("span", { class: "dev-propname", text: prop.replace(/-color$/, "") }),
          bezVyplne
            ? el("span", { class: "dev-note", text: "bez výplne (priehľadná)" })
            : colorControl({
                value,
                changed: overridden,
                note: paletteKey ? `paleta: ${PALETTE_LABELS[paletteKey] || paletteKey}` : "",
                onInput: (v) => {
                  setLayerPaint(layer.id, prop, v);
                  apply({ rerender: false });
                },
                onReset: overridden
                  ? () => {
                      setLayerPaint(layer.id, prop, undefined);
                      apply({ immediate: true });
                    }
                  : null
              })
        ])
      );
      if (canNoFill) {
        const box = el("input", { type: "checkbox", class: "dev-check" });
        box.checked = !!bezVyplne;
        box.addEventListener("change", () => {
          setLayerPaint(layer.id, prop, box.checked ? NO_FILL : undefined);
          apply({ immediate: true });
        });
        parts.push(
          el("label", { class: `dev-field dev-inline${bezVyplne ? " changed" : ""}` }, [
            box,
            el("span", { text: "bez výplne – ostane len vzor a okraj" })
          ])
        );
      }
      parts.push(
        el("div", { class: "dev-prop" }, [
          el("span", { class: "dev-propname", text: "podľa zoomu" }),
          zoomStopsEditor({ layer, prop, kind: "color" })
        ])
      );
    }

    // ---- farby, ktoré vrstva vyberá výrazom ----
    // Napr. pásik trasy má farbu podľa značky z OSM, takže v `paint` nie je
    // hex, ale `match`. Taká farba sa nedá prepísať na vrstve – mení sa
    // v palete, a tá platí pre celú tému. Ovládanie je aj tak tu, nech sa
    // farba ladí tam, kde je vidieť, čo mení.
    const extraKeys = (layer.metadata || {})["frico:palette-extra"] || [];
    if (extraKeys.length) {
      const colors = mergedPalette(getTheme(), overrides);
      const changedPalette = overrides.palette[getTheme()] || {};
      parts.push(
        el("div", { class: "dev-prop dev-prophead" }, [
          el("span", {
            class: "dev-propname",
            text: "farby z palety (platia pre celú tému)"
          })
        ])
      );
      for (const key of extraKeys) {
        parts.push(
          el("div", { class: "dev-prop" }, [
            el("span", { class: "dev-propname", text: PALETTE_LABELS[key] || key }),
            colorControl({
              value: colors[key],
              changed: key in changedPalette,
              onInput: (v) => {
                setPaletteColor(key, v);
                apply({ rerender: false });
              },
              onReset:
                key in changedPalette
                  ? () => {
                      setPaletteColor(key, undefined);
                      apply({ immediate: true });
                    }
                  : null
            })
          ])
        );
      }
    }

    // ---- ikona ----
    // Len tam, kde je meno ikony v štýle napevno – mená skladané výrazom
    // (POI podľa triedy) sa vyberajú z dát, nie zo zoznamu.
    const iconNow = (layer.layout || {})["icon-image"];
    if (layer.type === "symbol" && typeof iconNow === "string") {
      parts.push(sectionTitle("Ikona"));
      const set = (getIconSets?.() || []).find(
        (s) => s.id === selectedIconSource(overrides)
      ) || (getIconSets?.() || [])[0];
      // Súčasná ikona je v zozname vždy, aj keby ju nasadená sada nemala –
      // inak by `select` ticho ukázal prvú položku a tvrdil, že je vybraná.
      const names = pickableIcons(set, iconNow);
      parts.push(
        el("div", { class: `dev-sub${o.icon ? " changed" : ""}` }, [
          selectField({
            label: "Ikona",
            value: iconNow,
            options: names.map((n) => [n, n]),
            onChange: (v) => {
              setLayerOverride(layer.id, { icon: v === iconNow && !o.icon ? undefined : v });
              apply({ immediate: true });
            }
          }),
          o.icon
            ? el("button", {
                type: "button",
                class: "dev-mini",
                title: "Späť na pôvodnú ikonu",
                text: "⟲",
                onclick: () => {
                  setLayerOverride(layer.id, { icon: undefined });
                  apply({ immediate: true });
                }
              })
            : null
        ])
      );
    }

    // ---- sila tieňovania reliéfu ----
    // Jediné číslo mimo trojice farba/krytie/hrúbka, ktoré úpravy poznajú.
    // Je tu preto, že „silnejšie tieňovanie" sa ladí okom a po jednom zoome:
    // na prehľade stačí naznačiť tvar pohoria, v detaile má reliéf niesť to
    // hlavné – a to sú dve rôzne hodnoty, nie jedna.
    if (layer.type === "hillshade") {
      parts.push(
        sectionTitle("Sila tieňovania", "0 = plocho, 1 = najsilnejšie"),
        el("div", { class: "dev-sub" }, [
          paintNumber({
            layer,
            prop: "hillshade-exaggeration",
            label: "sila",
            min: 0,
            max: 1,
            step: 0.05
          })
        ]),
        el("div", { class: "dev-prop" }, [
          el("span", { class: "dev-propname", text: "sila podľa zoomu" }),
          zoomStopsEditor({
            layer, prop: "hillshade-exaggeration", kind: "number",
            min: 0, max: 1, step: 0.05
          })
        ])
      );
    }

    if (!canDecorate(layer)) return el("div", { class: "dev-details" }, parts);

    const isLine = layer.type === "line";
    parts.push(
      sectionTitle(
        isLine ? "Štýl čiary" : "Štýl plochy",
        isLine ? "plná, čiarkovaná, bodkovaná…" : "výplň a vzor"
      )
    );

    // ---- prerušovanie a hrúbka čiary ----
    // Toto je to, čím sa náučný chodník odlíši od zvyšku: druh čiary
    // (`line-dasharray`) plus jej hrúbka a krytie.
    if (isLine) {
      parts.push(
        el("div", { class: `dev-sub${o.dash ? " changed" : ""}` }, [
          dashField({
            label: "Čiara",
            value: o.dash || "solid",
            onChange: (v) => {
              setLayerOverride(layer.id, { dash: v === "solid" ? undefined : v });
              apply({ immediate: true });
            }
          }),
          paintNumber({
            layer,
            prop: "line-width",
            label: "hrúbka",
            // NIE 0: nulová hrúbka čiaru nekreslí a v mape to vyzerá ako
            // chýbajúce dáta, nie ako vypnutá vrstva. Na vypnutie je 👁.
            min: 0.1,
            max: 40,
            step: 0.5
          }),
          paintNumber({
            layer,
            prop: "line-opacity",
            label: "krytie",
            min: 0,
            max: 1,
            step: 0.1
          })
        ])
      );
      // Hrúbka a krytie sa dajú nastaviť aj PODĽA ZOOMU – práve tie dve sa
      // ladia najčastejšie (tenko na prehľade, hrubo v detaile).
      for (const [prop, label, min, max, step] of [
        ["line-width", "hrúbka podľa zoomu", 0.1, 40, 0.5],
        ["line-opacity", "krytie podľa zoomu", 0, 1, 0.1]
      ]) {
        parts.push(
          el("div", { class: "dev-prop" }, [
            el("span", { class: "dev-propname", text: label }),
            zoomStopsEditor({ layer, prop, kind: "number", min, max, step })
          ])
        );
      }
    } else {
      // Plocha: krytie výplne. Vzor a okraj sú nižšie – spolu je z toho
      // „šrafovaná plocha s prerušovaným okrajom", ktorá sa dá naklikať.
      const opacityProp =
        layer.type === "fill-extrusion" ? "fill-extrusion-opacity" : "fill-opacity";
      parts.push(
        el("div", { class: "dev-sub" }, [
          paintNumber({ layer, prop: opacityProp, label: "krytie", min: 0, max: 1, step: 0.05 })
        ]),
        el("div", { class: "dev-prop" }, [
          el("span", { class: "dev-propname", text: "krytie podľa zoomu" }),
          zoomStopsEditor({ layer, prop: opacityProp, kind: "number", min: 0, max: 1, step: 0.05 })
        ])
      );
    }

    // ---- opakujúci sa vzor ----
    // Vzor môže mať vrstva ZABUDOVANÝ V ŠTÝLE (`frico:pattern` – kamienky
    // v skalnej ploche). Ovládanie preto pracuje s ÚČINNÝM vzorom, nie
    // s úpravou: bez toho by rozbaľovačka nad skalami tvrdila „žiadny",
    // hoci v mape vzor je, a prvá zmena veľkosti by ho vyrobila odznova
    // s inou farbou.
    //
    // Tri stavy, ktoré sa musia dať rozlíšiť:
    //   kľúč chýba  … platí to, čo je v štýle
    //   `null`      … vzor zo štýlu je VYPNUTÝ (nie „nič nezmenené")
    //   predpis     … vlastný vzor
    const builtinPattern = (layer.metadata || {})["frico:pattern"] || null;
    const patChanged = "pattern" in o;
    const pat = patChanged ? o.pattern : builtinPattern;
    const patternRow = [
      selectField({
        label: "Vzor",
        value: pat ? pat.id : "",
        options: [["", "žiadny"], ...PATTERNS.map((p) => [p.id, p.label])],
        onChange: (v) => {
          setLayerOverride(layer.id, {
            pattern: v
              ? { ...(pat || { size: 16, weight: 1, opacity: 1, color: darken(primaryColor(layer)) }), id: v }
              // „Žiadny" nad vrstvou so vzorom zo štýlu musí vzor vypnúť,
              // nie len zahodiť úpravu – tá by ho vrátila späť.
              : builtinPattern ? null : undefined
          });
          apply({ immediate: true });
        }
      })
    ];
    if (pat) {
      // Doladenie vychádza z ÚČINNÉHO vzoru (`pat`), nie z prázdna – inak by
      // posun veľkosti nad skalnou plochou zabudol jej farbu aj krytie.
      const patch = (p) => {
        patchSub(layer.id, "pattern", p, pat);
        apply({ immediate: true });
      };
      patternRow.push(
        colorControl({
          value: pat.color,
          changed: patChanged,
          onInput: (v) => {
            patchSub(layer.id, "pattern", { color: v }, pat);
            apply({ rerender: false });
          }
        }),
        numberField({
          label: "veľkosť",
          value: pat.size,
          min: 4,
          max: 64,
          step: 1,
          onChange: (v) => patch({ size: v ?? 16 })
        }),
        numberField({
          label: "hrúbka",
          value: pat.weight,
          min: 0.5,
          max: 8,
          step: 0.5,
          onChange: (v) => patch({ weight: v ?? 1 })
        }),
        numberField({
          label: "krytie",
          value: pat.opacity,
          min: 0,
          max: 1,
          step: 0.1,
          onChange: (v) => patch({ opacity: v ?? 1 })
        })
      );
    }
    // „Zmenené" je úprava vzoru, nie vzor samotný: kamienky zo štýlu sú
    // predvolený stav mapy, nie niečo, čo v tejto relácii niekto naklikal.
    parts.push(el("div", { class: `dev-sub${patChanged ? " changed" : ""}` }, patternRow));

    // ---- okraj ----
    const out = o.outline;
    const isArea = layer.type !== "line";
    parts.push(
      sectionTitle("Okraj", isArea ? "obrys plochy" : "širší obrys pod čiarou")
    );
    const outlineRow = [
      selectField({
        label: "Okraj",
        value: out ? "on" : "off",
        options: [
          ["off", "žiadny"],
          ["on", isArea ? "obrys plochy" : "obrys pod čiarou"]
        ],
        onChange: (v) => {
          setLayerOverride(layer.id, {
            outline:
              v === "on"
                ? out || { color: darken(primaryColor(layer)), width: isArea ? 1.5 : 1, opacity: 1 }
                : undefined
          });
          apply({ immediate: true });
        }
      })
    ];
    if (out) {
      outlineRow.push(
        colorControl({
          value: out.color,
          changed: true,
          onInput: (v) => {
            patchSub(layer.id, "outline", { color: v });
            apply({ rerender: false });
          }
        }),
        numberField({
          label: "šírka",
          value: out.width,
          min: 0.5,
          max: 40,
          step: 0.5,
          onChange: (v) => {
            patchSub(layer.id, "outline", { width: v ?? 1 });
            apply({ immediate: true });
          }
        }),
        dashField({
          label: "čiara",
          value: out.dash || "solid",
          onChange: (v) => {
            patchSub(layer.id, "outline", { dash: v === "solid" ? undefined : v });
            apply({ immediate: true });
          }
        }),
        numberField({
          label: "krytie",
          value: out.opacity,
          min: 0,
          max: 1,
          step: 0.1,
          onChange: (v) => {
            patchSub(layer.id, "outline", { opacity: v ?? 1 });
            apply({ immediate: true });
          }
        })
      );
    }
    parts.push(el("div", { class: `dev-sub${out ? " changed" : ""}` }, outlineRow));

    return el("div", { class: "dev-details" }, parts);
  }

  // ---------- tab: prvky ----------
  /** Hodnota atribútu do tabuľky – prázdne a dlhé sa musia dať rozoznať. */
  const attrText = (v) => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "object") return JSON.stringify(v);
    const s = String(v);
    return s === "" ? '""' : s;
  };

  /** Odkaz na prvok v OpenStreetMape, ak vieme jeho id. */
  function osmLink(props) {
    if (props.rel != null) {
      return ["relácia", `https://www.openstreetmap.org/relation/${props.rel}`];
    }
    // OpenMapTiles dlaždice id prvkov nenesú; `osm_id` býva len tam, kde si
    // ho schéma výslovne vypýtala.
    if (props.osm_id != null) {
      const id = Number(props.osm_id);
      if (Number.isFinite(id) && id > 0) {
        return ["prvok", `https://www.openstreetmap.org/way/${id}`];
      }
    }
    return null;
  }

  function trailRow(p) {
    const colours = mergedPalette(getTheme(), overrides);
    const type = TRAIL_TYPES.find((t) => t.id === p.route);
    // Tá istá cesta k farbe ako v štýle: pomenovaná značka → paleta,
    // neznámy hex z OSM → priamo, žiadna farba → podľa druhu trasy.
    const colour =
      colours[TRAIL_MARK_KEYS[p.colour]] ||
      p.hex ||
      colours[type?.palette] ||
      "#888888";
    const title = [p.ref, p.name].filter(Boolean).join(" ") || "(bez názvu)";
    // Značka, ako ju do dlaždíc zapísal `workers/trails/tags.py` – v teréne
    // je to práve ona, takže patrí do inšpektora hneď vedľa farby.
    const markLabel = p.mark
      ? `značka ${MARK_SHAPES.find((sh) => sh.id === p.mark)?.label || p.mark} ` +
        `(${p.mark_fg} na ${p.mark_bg === "yellow" ? "žltom" : p.mark_bg})`
      : "";
    const detail = [type?.short || p.route, p.colour || p.hex, markLabel, p.network, p.tier]
      .filter(Boolean)
      .join(" · ");
    const link = osmLink(p);
    return el("div", { class: "dev-prow" }, [
      el("span", { class: "dev-swatch", style: `background:${colour}` }),
      el("span", { class: "dev-name" }, [
        el("span", { text: title }),
        el("small", { text: `${detail}${p.off != null ? ` · pruh ${p.off}` : ""}` })
      ]),
      link
        ? el("a", {
            class: "dev-mini",
            href: link[1],
            target: "_blank",
            rel: "noopener",
            title: "Otvoriť v OpenStreetMape",
            text: "↗"
          })
        : null
    ]);
  }

  function featureItem(f, index) {
    const meta = (getStyle()?.layers || []).find((l) => l.id === f.layer.id)?.metadata || {};
    const props = f.properties || {};
    const keys = Object.keys(props).sort();
    const open = pickOpen.has(index);
    const kind = meta["frico:kind"] || "";
    const link = osmLink(props);

    const head = el("div", { class: "dev-row" }, [
      el("span", {
        class: `dev-kind${kind ? ` k-${kind}` : ""}`,
        text: KIND_LABELS[kind] || f.layer.type
      }),
      el("span", {
        class: "dev-name",
        onclick: () => {
          if (open) pickOpen.delete(index);
          else pickOpen.add(index);
          renderBody();
        }
      }, [
        el("span", { text: `${open ? "▾ " : "▸ "}${meta["frico:label"] || f.layer.id}` }),
        el("small", {
          text: `${f.layer.id}${f.sourceLayer ? ` · ${f.sourceLayer}` : ""} · ${keys.length} atribútov`
        })
      ]),
      el("button", {
        type: "button",
        class: "dev-mini",
        title: "Kopírovať prvok ako JSON",
        text: "⧉",
        onclick: (ev) =>
          copyText(
            JSON.stringify(
              { layer: f.layer.id, source: f.source, sourceLayer: f.sourceLayer, properties: props },
              null,
              2
            ),
            ev.currentTarget
          )
      }),
      el("button", {
        type: "button",
        class: "dev-mini",
        title: "Nájsť vrstvu v zozname vrstiev",
        text: "✎",
        onclick: () => {
          tab = "layers";
          search = f.layer.id;
          expanded.add(f.layer.id);
          render();
        }
      })
    ]);

    if (!open) return el("div", { class: "dev-item" }, [head]);

    const rows = keys.length
      ? keys.map((k) =>
          el("div", { class: "dev-kv" }, [
            el("b", { text: k }),
            el("span", { text: attrText(props[k]) })
          ])
        )
      : [el("p", { class: "dev-hint", text: "Prvok nemá žiadne atribúty." })];
    if (link) {
      rows.push(
        el("div", { class: "dev-kv" }, [
          el("b", { text: "v OSM" }),
          el("a", { href: link[1], target: "_blank", rel: "noopener", text: link[1] })
        ])
      );
    }
    return el("div", { class: "dev-item" }, [head, el("div", { class: "dev-details" }, rows)]);
  }

  function renderPick() {
    const hint = el("p", {
      class: "dev-hint",
      html:
        "Klikni do mapy a tu bude <b>všetko, čo je pod kurzorom</b> – plochy, " +
        "čiary, body aj popisky zo všetkých vrstiev naraz, s celým obsahom " +
        "dlaždice. Vypisuje sa len to, čo je na danom zoome naozaj vykreslené; " +
        "vybraté prvky sú v mape zvýraznené oranžovo."
    });

    const radius = numberField({
      label: "polomer (px)",
      value: pickRadius,
      min: 1,
      max: 40,
      step: 1,
      onChange: (v) => {
        pickRadius = Math.min(40, Math.max(1, v ?? 6));
        renderBody();
      }
    });

    if (!picked) {
      return el("div", {}, [
        hint,
        el("div", { class: "dev-bulk on" }, [radius]),
        el("p", { class: "dev-hint", text: "Zatiaľ nič – klikni do mapy." })
      ]);
    }

    const { lng, lat } = picked.lngLat;
    const coords = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
    const osmUrl = `https://www.openstreetmap.org/#map=${Math.round(picked.zoom)}/${lat.toFixed(5)}/${lng.toFixed(5)}`;

    const head = el("div", { class: "dev-bulk on" }, [
      el("span", { class: "dev-bulklabel", text: coords }),
      el("button", {
        type: "button",
        class: "dev-mini",
        title: "Kopírovať súradnice",
        text: "⧉",
        onclick: (ev) => copyText(coords, ev.currentTarget)
      }),
      el("a", {
        class: "dev-btn",
        href: osmUrl,
        target: "_blank",
        rel: "noopener",
        text: "Toto miesto v OSM"
      }),
      radius,
      el("button", {
        type: "button",
        class: "dev-btn danger",
        text: "Vyčistiť",
        onclick: clearPick
      })
    ]);

    const parts = [hint, head];

    // Značené trasy sa vypisujú zvlášť: pásiky sú posunuté vedľa cesty, takže
    // klik do cesty ich netrafí – a práve „ktoré trasy tadiaľto vedú" je to,
    // čo človek pri chodníku hľadá.
    if (picked.trails.length) {
      parts.push(
        el("h4", { class: "dev-h4", text: `Značené trasy tadiaľto (${picked.trails.length})` }),
        el("div", { class: "dev-list" }, picked.trails.map(trailRow))
      );
    }

    parts.push(
      el("h4", {
        class: "dev-h4",
        text: `Prvky pod kurzorom (${picked.features.length})`
      })
    );
    parts.push(
      picked.features.length
        ? el("div", { class: "dev-list" }, picked.features.map(featureItem))
        : el("p", {
            class: "dev-hint",
            text: "Na tomto mieste nie je vykreslený žiadny prvok – skús väčší polomer alebo iný zoom."
          })
    );

    return el("div", {}, parts);
  }

  // ---------- tab: paleta ----------
  function renderPalette() {
    const theme = getTheme();
    const colors = mergedPalette(theme, overrides);
    const changed = overrides.palette[theme] || {};

    const bulkColor = el("input", { type: "color", class: "dev-color", value: "#ff0000" });
    const bulk = el("div", { class: `dev-bulk${selectedPaletteKeys.size ? " on" : ""}` }, [
      el("span", { class: "dev-bulklabel", text: `Vybraných: ${selectedPaletteKeys.size}` }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Zrušiť výber",
        onclick: () => {
          selectedPaletteKeys.clear();
          renderBody();
        }
      }),
      bulkColor,
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Zafarbiť výber",
        onclick: () => {
          for (const key of selectedPaletteKeys) setPaletteColor(key, bulkColor.value);
          apply();
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Kopírovať výber",
        onclick: (ev) => {
          const out = {};
          for (const key of selectedPaletteKeys) out[key] = colors[key];
          copyText(JSON.stringify(out, null, 2), ev.currentTarget);
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn",
        text: "Vložiť farby",
        onclick: () => {
          const raw = prompt(
            "Vlož JSON s farbami palety, napr.\n{\"forest\": \"#a8cc8e\", \"grass\": \"#cbe0aa\"}"
          );
          if (!raw) return;
          try {
            for (const [k, v] of Object.entries(JSON.parse(raw))) {
              if (PALETTE_LABELS[k] && isHexColor(v)) setPaletteColor(k, normColor(v));
            }
            apply();
          } catch (err) {
            alert(`Nepodarilo sa prečítať JSON: ${err.message}`);
          }
        }
      }),
      el("button", {
        type: "button",
        class: "dev-btn danger",
        text: "Reset palety",
        onclick: () => {
          delete overrides.palette[theme];
          apply();
        }
      })
    ]);

    const groups = [];
    for (const group of PALETTE_GROUPS) {
      groups.push(el("div", { class: "dev-group" }, [
        el("span", { class: "dev-groupname", text: group.label }),
        el("button", {
          type: "button",
          class: "dev-mini",
          title: "Vybrať skupinu",
          text: "☑",
          onclick: () => {
            const keys = group.keys.map(([k]) => k);
            const all = keys.every((k) => selectedPaletteKeys.has(k));
            for (const k of keys) {
              if (all) selectedPaletteKeys.delete(k);
              else selectedPaletteKeys.add(k);
            }
            renderBody();
          }
        })
      ]));

      for (const [key, label] of group.keys) {
        const check = el("input", { type: "checkbox", class: "dev-check" });
        check.checked = selectedPaletteKeys.has(key);
        check.addEventListener("change", () => {
          if (check.checked) selectedPaletteKeys.add(key);
          else selectedPaletteKeys.delete(key);
          const el2 = body.querySelector(".dev-bulklabel");
          if (el2) el2.textContent = `Vybraných: ${selectedPaletteKeys.size}`;
        });

        groups.push(
          el("div", { class: `dev-prow${changed[key] ? " changed" : ""}` }, [
            check,
            el("span", { class: "dev-name", title: key }, [
              el("span", { text: label }),
              el("small", { text: key })
            ]),
            colorControl({
              value: colors[key],
              changed: !!changed[key],
              onInput: (v) => {
                setPaletteColor(key, v);
                apply({ rerender: false });
              },
              onReset: changed[key]
                ? () => {
                    setPaletteColor(key, undefined);
                    apply({ immediate: true });
                  }
                : null
            })
          ])
        );
      }
    }

    return el("div", {}, [
      el("p", {
        class: "dev-hint",
        text: `Farby témy „${THEMES[theme].label}". Zmena tu prefarbí naraz všetky vrstvy, ktoré farbu používajú.`
      }),
      bulk,
      el("div", { class: "dev-list" }, groups)
    ]);
  }

  // ---------- tab: trasy ----------
  // Značené trasy sa nedajú poriadne ladiť v záložke Vrstiev: jeden druh
  // trasy sú tam TRI vrstvy (pásik, ikona, názov), farba nie je v `paint`, ale
  // vo výraze podľa značky z OSM, a odstup od cesty je vlastnosť všetkých
  // naraz. Táto záložka je preto o druhu trasy, nie o vrstve.

  /** Nastavenie jedného druhu trasy (`overrides.trails.types[id]`). */
  function setTrailType(id, patch) {
    const cur = { ...(overrides.trails.types[id] || {}), ...patch };
    for (const k of Object.keys(cur)) if (cur[k] === undefined) delete cur[k];
    if (Object.keys(cur).length) overrides.trails.types[id] = cur;
    else delete overrides.trails.types[id];
  }

  /** Rozostup a veľkosť značiek; `undefined` = späť na predvolené. */
  function setTrailMarks(key, value) {
    if (value === undefined || value === null || value === "" ||
        Number(value) === TRAIL_MARK_DEFAULTS[key]) {
      delete overrides.trails.marks[key];
    } else {
      overrides.trails.marks[key] = Number(value);
    }
  }

  /** Odstup pásikov od cesty; `undefined` = späť na predvolený. */
  function setTrailGap(key, value) {
    if (value === undefined || value === null || value === "" ||
        Number(value) === TRAIL_GAP_DEFAULTS[key]) {
      delete overrides.trails.gap[key];
    } else {
      overrides.trails.gap[key] = Number(value);
    }
  }

  /** Mená ikon z nasadenej sady (aj s tou, ktorú štýl práve používa). */
  function iconNames(extra) {
    const set = (getIconSets?.() || []).find(
      (s2) => s2.id === selectedIconSource(overrides)
    ) || (getIconSets?.() || [])[0];
    return pickableIcons(set, extra);
  }

  /** Ktorá ikona je pre daný druh trasy naozaj v mape (z hotového štýlu). */
  function trailIconNow(id) {
    const layer = getStyle().layers.find((l) => l.id === `trail-${id}-icon`);
    const name = (layer?.layout || {})["icon-image"];
    return typeof name === "string" ? name : "";
  }

  function renderTrails() {
    const theme = getTheme();
    const colors = mergedPalette(theme, overrides);
    const changedPalette = overrides.palette[theme] || {};
    const gaps = trailGapPx(overrides);

    // ---- odstup od cesty ----
    const gapFields = [
      ["road", "Pri ceste", "pásik ide tesne za okraj cesty aj s jej obrysom"],
      ["path", "Pri chodníku a lesnej ceste", "jemný odstup, nech je pod pásikom vidieť aj chodník"],
      ["pitch", "Rozostup dvoch trás",
        "predvolené = šírka pásika, čiže nalepené na seba; menej znamená, " +
        "že sa prekrývajú a spodná farba nie je vidieť"]
    ].map(([key, label, note]) =>
      el("div", { class: `dev-sub${overrides.trails.gap[key] != null ? " changed" : ""}` }, [
        numberField({
          label,
          value: gaps[key],
          min: 0,
          max: 60,
          step: 0.1,
          onChange: (v) => {
            setTrailGap(key, v);
            apply({ immediate: true });
          }
        }),
        el("span", { class: "dev-note", text: `${note} · predvolené ${TRAIL_GAP_DEFAULTS[key]} px` })
      ])
    );

    // ---- značky v teréne ----
    // Rozostup je to, čo z „v pravidelných intervaloch" naozaj rozhoduje:
    // menší = značka je vidieť častejšie, ale trasa sa zaplní štvorcami.
    const marks = trailMarkPx(overrides);
    const markFields = [
      ["spacing", "Rozostup značiek", 20, 2000, 5,
        "vzdialenosť dvoch značiek na obrazovke; každý druh trasy ho má " +
        "o kúsok iný, aby si značky dvoch trás na tej istej ceste nesadli na seba"],
      ["size", "Veľkosť značky", 0.2, 4, 0.05,
        "násobok predvolenej veľkosti štvorca"]
    ].map(([key, label, min, max, step, note]) =>
      el("div", { class: `dev-sub${overrides.trails.marks[key] != null ? " changed" : ""}` }, [
        numberField({
          label,
          value: marks[key],
          min,
          max,
          step,
          onChange: (v) => {
            setTrailMarks(key, v);
            apply({ immediate: true });
          }
        }),
        el("span", { class: "dev-note", text: `${note} · predvolené ${TRAIL_MARK_DEFAULTS[key]}` })
      ])
    );

    // ---- druhy trás ----
    const typeRows = TRAIL_TYPES.map((type) => {
      const def = trailTypeDef(type, overrides);
      const own = overrides.trails.types[type.id] || {};
      const icons = iconNames(trailIconNow(type.id));
      return el("div", { class: `dev-item${Object.keys(own).length ? " changed" : ""}` }, [
        el("div", { class: "dev-row" }, [
          el("span", { class: "dev-swatch", style: `background:${colors[type.palette]}` }),
          el("span", { class: "dev-name" }, [
            el("span", { text: type.label }),
            el("small", {
              text: `${type.id} · ${type.side > 0 ? "vpravo od cesty" : "vľavo od cesty (opačná strana než pešie)"}`
            })
          ])
        ]),
        el("div", { class: "dev-sub" }, [
          el("span", { class: "dev-note", text: "Farba bez značky" }),
          colorControl({
            value: colors[type.palette],
            changed: !!changedPalette[type.palette],
            onInput: (v) => {
              setPaletteColor(type.palette, v);
              apply({ rerender: false });
            },
            onReset: changedPalette[type.palette]
              ? () => {
                  setPaletteColor(type.palette, undefined);
                  apply({ immediate: true });
                }
              : null
          })
        ]),
        el("div", { class: `dev-sub${own.dash ? " changed" : ""}` }, [
          dashField({
            label: "Čiara",
            value: def.dash,
            onChange: (v) => {
              setTrailType(type.id, { dash: v === type.dash ? undefined : v });
              apply({ immediate: true });
            }
          }),
          own.dash
            ? el("button", {
                type: "button",
                class: "dev-mini",
                title: "Späť na pôvodnú čiaru",
                text: "⟲",
                onclick: () => {
                  setTrailType(type.id, { dash: undefined });
                  apply({ immediate: true });
                }
              })
            : null
        ]),
        el("div", { class: `dev-sub${own.mark != null ? " changed" : ""}` }, [
          selectField({
            label: "Značka",
            // Tri odpovede, nie dve: prázdna položka je „taká, aká je v OSM"
            // (`osmc:symbol` – pásová, vrcholová, bicykel), „žiadna" značky
            // vypne a meno tvaru ich všetky prekreslí na jeden.
            value: own.mark != null ? own.mark || "ziadna" : "",
            options: [
              ["", "— podľa OSM —"],
              ["ziadna", "— žiadna —"],
              ...MARK_SHAPES.map((sh) => [sh.id, sh.label])
            ],
            onChange: (v) => {
              setTrailType(type.id, {
                mark: v === "" ? undefined : (v === "ziadna" ? "" : v)
              });
              apply({ immediate: true });
            }
          }),
          el("span", {
            class: "dev-note",
            text: "biely (pešia) alebo žltý (cyklo) štvorec s farebným pásom – " +
              "farbu aj podklad berie z OSM, tvar sa dá prekresliť"
          })
        ]),
        el("div", { class: `dev-sub${own.icon != null ? " changed" : ""}` }, [
          selectField({
            label: "Ikona",
            // Prázdna položka je „žiadna" – trasa hustá na ikonky sa inak
            // nedá zbaviť inak než skrytím celej vrstvy.
            value: own.icon != null ? own.icon : trailIconNow(type.id),
            options: [["", "— žiadna —"], ...icons.map((n) => [n, n])],
            // Ikonka sa kreslí len tam, kde trasa značku NEMÁ (neznáma farba
            // z OSM) – inak by na jednej čiare boli dva symboly naraz.
            onChange: (v) => {
              setTrailType(type.id, { icon: v });
              apply({ immediate: true });
            }
          }),
          own.icon != null
            ? el("button", {
                type: "button",
                class: "dev-mini",
                title: "Späť na pôvodnú ikonu",
                text: "⟲",
                onclick: () => {
                  setTrailType(type.id, { icon: undefined });
                  apply({ immediate: true });
                }
              })
            : null
        ])
      ]);
    });

    // ---- farby značiek ----
    // Turistická značka farbu NESIE z OSM (červená, modrá…), takže farba
    // druhu trasy je pri nej len náhrada pre trasy bez značky. Skutočné
    // farby značiek sú tieto – a patria sem, nie do palety niekde inde.
    const markRows = TRAIL_MARK_COLOURS.map(([name, key]) =>
      el("div", { class: `dev-prow${changedPalette[key] ? " changed" : ""}` }, [
        el("span", { class: "dev-swatch", style: `background:${colors[key]}` }),
        el("span", { class: "dev-name", title: key }, [
          el("span", { text: PALETTE_LABELS[key] || name }),
          el("small", { text: `colour=${name}` })
        ]),
        colorControl({
          value: colors[key],
          changed: !!changedPalette[key],
          onInput: (v) => {
            setPaletteColor(key, v);
            apply({ rerender: false });
          },
          onReset: changedPalette[key]
            ? () => {
                setPaletteColor(key, undefined);
                apply({ immediate: true });
              }
            : null
        })
      ])
    );

    return el("div", {}, [
      el("p", {
        class: "dev-hint",
        text:
          "Značené trasy sa kreslia ako farebné pásiky vedľa cesty, nie na nej – " +
          "samotná cesta tak zostane vidieť. Pešie trasy idú na jednu stranu, " +
          "cyklo a MTB na druhú; druhá trasa na tej istej ceste sa nalepí na prvú."
      }),
      el("div", { class: "dev-h5" }, [
        el("span", { text: "Odstup od cesty" }),
        el("small", { text: `v pixeloch pri z${TRAIL_GAP_ZOOM}, ostatné zoomy sa škálujú s ním` })
      ]),
      ...gapFields,
      el("div", { class: "dev-bulk on" }, [
        el("button", {
          type: "button",
          class: "dev-btn danger",
          text: "Späť na predvolené odstupy a značky",
          onclick: () => {
            overrides.trails.gap = {};
            overrides.trails.marks = {};
            apply();
          }
        })
      ]),
      el("div", { class: "dev-h5" }, [
        el("span", { text: "Značky v teréne" }),
        el("small", { text: `v pixeloch pri z${TRAIL_MARK_ZOOM}, ostatné zoomy sa škálujú s ním` })
      ]),
      ...markFields,
      el("div", { class: "dev-h5" }, [
        el("span", { text: "Druhy trás" }),
        el("small", { text: "farba, čiara, značka a ikona podľa druhu (route)" })
      ]),
      el("div", { class: "dev-list" }, typeRows),
      el("div", { class: "dev-h5" }, [
        el("span", { text: "Farby značiek" }),
        el("small", { text: "turistická značka nesie farbu z OSM – toto sú tie farby" })
      ]),
      el("div", { class: "dev-list" }, markRows),
      el("div", { class: "dev-bulk on" }, [
        el("button", {
          type: "button",
          class: "dev-btn danger",
          text: "Späť na pôvodné trasy",
          onclick: () => {
            overrides.trails = { gap: {}, types: {}, marks: {} };
            for (const [, key] of TRAIL_MARK_COLOURS) setPaletteColor(key, undefined);
            for (const t of TRAIL_TYPES) setPaletteColor(t.palette, undefined);
            apply();
          }
        })
      ])
    ]);
  }

  // ---------- tab: štítky ciest ----------
  // Štítok s číslom cesty („D1", „I/18") sa nedá ladiť v záložke Vrstiev
  // rovnako ako trasa: farba ani tvar nie sú v `paint`, sú UPEČENÉ do obrázka
  // v sprite (`workers/assets/shields.mjs`). V prehliadači sa preto dá
  // prepnúť TVAR – všetky tvary sú v sprite naraz, takže je to zmena mena
  // obrázka; farba štítka sa mení v palete a prejaví sa až po builde.

  /** Tvar štítka jednej triedy cesty; `undefined` = späť na predvolený. */
  function setShieldShape(id, shape) {
    if (shape === undefined) delete overrides.shields[id];
    else overrides.shields[id] = { shape };
  }

  function renderShields() {
    const theme = getTheme();
    const colors = mergedPalette(theme, overrides);
    const changedPalette = overrides.palette[theme] || {};

    const rows = SHIELD_DEFS.map(([id, label, classes, colorKey, mz, shapeId, textKey]) => {
      const shape = shieldShapeFor(id, shapeId, overrides);
      const own = overrides.shields[id];
      return el("div", { class: `dev-item${own ? " changed" : ""}` }, [
        el("div", { class: "dev-row" }, [
          // Náhľad je obyčajný HTML štvorček vo farbách štítka – obrázok
          // v sprite je až v mape a tu ide o to, ktorý riadok je ktorý.
          el("span", {
            class: "dev-swatch",
            style: `background:${colors[colorKey]};color:${colors[textKey]};` +
              `border-radius:${shape === "shield-round" ? "50%" : "3px"}`
          }),
          el("span", { class: "dev-name" }, [
            el("span", { text: label }),
            el("small", { text: `${classes ? classes.join(", ") : "podľa siete"} · od z${mz}` })
          ])
        ]),
        el("div", { class: `dev-sub${own ? " changed" : ""}` }, [
          selectField({
            label: "Tvar štítka",
            value: shape,
            options: SHIELD_SHAPES.map((sh) => [sh.id, sh.label]),
            onChange: (v) => {
              setShieldShape(id, v === shapeId ? undefined : v);
              apply({ immediate: true });
            }
          }),
          own
            ? el("button", {
                type: "button",
                class: "dev-mini",
                title: "Späť na pôvodný tvar",
                text: "⟲",
                onclick: () => {
                  setShieldShape(id, undefined);
                  apply({ immediate: true });
                }
              })
            : null
        ]),
        el("div", { class: "dev-sub" }, [
          el("span", { class: "dev-note", text: "Farba štítka" }),
          colorControl({
            value: colors[colorKey],
            changed: !!changedPalette[colorKey],
            onInput: (v) => {
              setPaletteColor(colorKey, v);
              apply({ rerender: false });
            },
            onReset: changedPalette[colorKey]
              ? () => {
                  setPaletteColor(colorKey, undefined);
                  apply({ immediate: true });
                }
              : null
          })
        ])
      ]);
    });

    return el("div", {}, [
      el("p", {
        class: "dev-hint",
        text:
          "Číslo cesty je značka, nie text – má podklad, stojí narovno a opakuje " +
          "sa po celej ceste. Podklad je obrázok upečený do spritu, takže sa tu " +
          "prepína jeho TVAR; farba sa v mape prejaví až po prebuildovaní spritu " +
          "(v prehliadači je vidieť len na náhľade vľavo)."
      }),
      el("div", { class: "dev-list" }, rows),
      el("div", { class: "dev-bulk on" }, [
        el("button", {
          type: "button",
          class: "dev-btn danger",
          text: "Späť na pôvodné štítky",
          onclick: () => {
            overrides.shields = {};
            for (const [, , , colorKey, , , textKey, borderKey] of SHIELD_DEFS) {
              for (const key of [colorKey, textKey, borderKey]) setPaletteColor(key, undefined);
            }
            apply();
          }
        })
      ])
    ]);
  }

  // ---------- tab: ikony ----------
  /**
   * Náhľad ikoniek: sprite je SDF, takže alfa nesie vzdialenostné pole a
   * hrana symbolu leží na 0,75. Prekreslíme ho na plnú farbu, nech je vidieť,
   * ako budú ikony na mape naozaj vyzerať.
   */
  function drawPreview(canvas, set, names, color) {
    const image = spriteImages.get(set.id);
    if (!image || !image.complete || !image.naturalWidth) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
    let x = 0;
    for (const [, e] of names) {
      const w = e.width;
      const h = e.height;
      const tmp = document.createElement("canvas");
      tmp.width = w;
      tmp.height = h;
      const tctx = tmp.getContext("2d");
      tctx.drawImage(image, e.x, e.y, w, h, 0, 0, w, h);
      const data = tctx.getImageData(0, 0, w, h);
      for (let i = 0; i < data.data.length; i += 4) {
        const a = data.data[i + 3];
        // 191 = hrana SDF; úzky prechod okolo nej dá vyhladený okraj.
        const alpha = a >= 200 ? 255 : a >= 175 ? ((a - 175) * 255) / 25 : 0;
        data.data[i] = r;
        data.data[i + 1] = g;
        data.data[i + 2] = b;
        data.data[i + 3] = alpha;
      }
      tctx.putImageData(data, 0, 0);
      const scale = Math.min(1, (canvas.height - 2) / h);
      ctx.drawImage(tmp, x, (canvas.height - h * scale) / 2, w * scale, h * scale);
      x += w * scale + 4;
      if (x > canvas.width) break;
    }
  }

  const PREVIEW_CLASSES = [
    "restaurant", "cafe", "fuel", "hospital", "bank", "lodging", "museum",
    "picnic_site", "campsite", "information", "shop", "bus", "railway",
    "park", "pharmacy", "post", "swimming", "mountain"
  ];

  function renderIcons() {
    const sets = getIconSets ? getIconSets() : [];
    const current = selectedIconSource(overrides);
    const color = mergedPalette(getTheme(), overrides).poiIcon || "#444444";

    if (!sets.length) {
      return el("p", {
        class: "dev-hint",
        text: "Nenašla sa žiadna nasadená sada ikoniek."
      });
    }

    const cards = sets.map((set) => {
      const radio = el("input", { type: "radio", name: "dev-iconset", class: "dev-check" });
      radio.checked = set.id === current;
      radio.addEventListener("change", () => {
        overrides.icons = set.id;
        apply({ immediate: true });
      });

      const canvas = el("canvas", { class: "dev-preview", width: "360", height: "26" });
      // Ukážeme len ikony, ktoré sada naozaj má.
      const index = set.index || null;
      const names = PREVIEW_CLASSES.map((c) => `${c}${set.suffix || ""}`)
        .filter((n) => set.icons.includes(n))
        .slice(0, 14);
      if (names.length) {
        const load = () => {
          const img = spriteImages.get(set.id);
          if (!img) return;
          drawPreview(
            canvas,
            set,
            names.map((n) => [n, (index || {})[n]]).filter(([, e]) => e),
            color
          );
        };
        if (!spriteImages.has(set.id)) {
          const img = new Image();
          img.crossOrigin = "anonymous";
          img.onload = () => renderBody();
          img.src = `${set.spriteUrl}.png`;
          spriteImages.set(set.id, img);
        } else {
          load();
        }
      }

      return el("label", { class: `dev-iconset${set.id === current ? " on" : ""}` }, [
        el("div", { class: "dev-row" }, [
          radio,
          el("span", { class: "dev-name" }, [
            el("span", { text: set.label }),
            el("small", { text: `${set.count} obrázkov · ${set.license || "?"}` })
          ]),
          set.sdf
            ? el("span", { class: "dev-kind k-point", text: "farbiteľné" })
            : el("span", { class: "dev-kind", text: "bez farby" })
        ]),
        names.length ? canvas : null,
        set.note ? el("p", { class: "dev-hint", text: set.note }) : null,
        set.source
          ? el("a", { class: "dev-note", href: set.source, target: "_blank", rel: "noreferrer", text: set.source })
          : null
      ]);
    });

    return el("div", {}, [
      el("p", {
        class: "dev-hint",
        text:
          "Sada ikoniek pre POI, vrcholy a letiská. Pipeline z každého zdroja " +
          "vyrobí SDF sprite bez podkladov, takže sa dajú prepínať naživo a " +
          "vybraná sada sa zapečie do štýlu pre web aj iOS."
      }),
      ...cards
    ]);
  }

  // ---------- tab: POI ----------
  function scanPoiClasses() {
    const map2 = getMap();
    if (!map2) return [];
    const counts = new Map();
    let features = [];
    try {
      features = map2.querySourceFeatures("omt", { sourceLayer: "poi" });
    } catch {
      features = [];
    }
    for (const f of features) {
      const key = f.properties?.subclass || f.properties?.class;
      if (key) counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }

  /** Skryté POI triedy: spoločné aj tie pre práve zobrazený typ mapy. */
  const poiHiddenAll = () =>
    new Set([...overrides.poi.hidden, ...(mapBucket()?.poi?.hidden || [])]);

  function setPoiHidden(cls, hide) {
    const bucket = editScope === "all" ? overrides.poi : mapBucket(true).poi;
    const next = new Set(bucket.hidden);
    if (hide) next.add(cls);
    else next.delete(cls);
    bucket.hidden = [...next].sort();
    pruneMaps();
  }

  function renderPoi() {
    const hidden = poiHiddenAll();
    const inBase = new Set(overrides.poi.hidden);
    const known = new Map(poiClasses);
    for (const h of hidden) if (!known.has(h)) known.set(h, 0);
    const list = [...known.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

    const rows = list.map(([cls, count]) => {
      const check = el("input", { type: "checkbox", class: "dev-check" });
      check.checked = !hidden.has(cls);
      check.addEventListener("change", () => {
        setPoiHidden(cls, !check.checked);
        apply({ rerender: false });
      });
      // Trieda skrytá spoločne sa v rozsahu „len táto mapa" vrátiť nedá –
      // nech je jasné, prečo odškrtnutie nič neurobí.
      const stuck = editScope === "map" && inBase.has(cls);
      return el("label", { class: `dev-prow${hidden.has(cls) ? " off" : ""}` }, [
        check,
        el("span", { class: "dev-name" }, [
          el("span", { text: cls }),
          el("small", {
            text: stuck
              ? "skryté pre všetky mapy – prepni rozsah"
              : count
              ? `${count} v zobrazenom výreze`
              : "skryté"
          })
        ])
      ]);
    });

    return el("div", {}, [
      el("p", {
        class: "dev-hint",
        text:
          "Ktoré body sa zobrazujú. Zoznam sa načíta z dlaždíc v aktuálnom výreze – " +
          "prejdi mapu tam, kde POI chceš, a načítaj znova."
      }),
      scopeBar(),
      el("div", { class: "dev-bulk on" }, [
        el("button", {
          type: "button",
          class: "dev-btn",
          text: "Načítať triedy z mapy",
          onclick: () => {
            poiClasses = scanPoiClasses();
            renderBody();
          }
        }),
        el("button", {
          type: "button",
          class: "dev-btn danger",
          text: editScope === "all" ? "Zobraziť všetky (všade)" : "Zobraziť všetky (táto mapa)",
          onclick: () => {
            if (editScope === "all") overrides.poi.hidden = [];
            else if (mapBucket()) mapBucket().poi.hidden = [];
            pruneMaps();
            apply();
          }
        })
      ]),
      rows.length
        ? el("div", { class: "dev-list" }, rows)
        : el("p", { class: "dev-hint", text: "Zatiaľ nič – klikni na tlačidlo Načítať triedy z mapy." })
    ]);
  }

  // ---------- tab: súbor ----------
  function renderFile() {
    const json = serializeOverrides(overrides);
    const area = el("textarea", { class: "dev-json", spellcheck: "false" });
    area.value = json;

    const fileInput = el("input", { type: "file", accept: ".json,application/json", class: "dev-file" });
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      try {
        const parsed = JSON.parse(await file.text());
        const { overrides: clean, problems } = normalizeOverrides(parsed);
        overrides = clean;
        apply({ immediate: true });
        if (problems.length) alert(`Načítané s výhradami:\n\n${problems.join("\n")}`);
      } catch (err) {
        alert(`Súbor sa nepodarilo načítať: ${err.message}`);
      }
    });

    return el("div", {}, [
      el("p", {
        class: "dev-hint",
        html:
          "Úpravy sú priebežne uložené v prehliadači. Stiahni ich ako " +
          "<code>style-overrides.json</code> a nahraj cez workflow " +
          "<b>Mapa · úpravy štýlu</b> (Actions → Run workflow → " +
          "vlož obsah súboru). Pipeline ich zapečie do mapy pre web aj iOS.<br>" +
          "V súbore je <code>layers</code> a <code>poi</code> spoločné pre " +
          "všetky typy máp, <code>maps</code> drží to, čo platí len pre jednu."
      }),
      el("div", { class: "dev-bulk on" }, [
        el("button", {
          type: "button",
          class: "dev-btn",
          text: "⬇ Stiahnuť style-overrides.json",
          onclick: () => {
            const blob = new Blob([serializeOverrides(overrides)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = el("a", { href: url, download: "style-overrides.json" });
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }
        }),
        el("button", {
          type: "button",
          class: "dev-btn",
          text: "⧉ Kopírovať JSON",
          onclick: (ev) => copyText(serializeOverrides(overrides), ev.currentTarget)
        }),
        el("button", {
          type: "button",
          class: "dev-btn",
          text: "Použiť z textu",
          onclick: () => {
            try {
              const { overrides: clean, problems } = normalizeOverrides(JSON.parse(area.value));
              overrides = clean;
              apply({ immediate: true });
              if (problems.length) alert(`Použité s výhradami:\n\n${problems.join("\n")}`);
            } catch (err) {
              alert(`Neplatný JSON: ${err.message}`);
            }
          }
        }),
        el("button", {
          type: "button",
          class: "dev-btn danger",
          text: "Vymazať všetky zmeny",
          onclick: () => {
            if (!confirm("Naozaj zahodiť všetky úpravy štýlu?")) return;
            overrides = emptyOverrides();
            selectedLayers.clear();
            selectedPaletteKeys.clear();
            apply({ immediate: true });
          }
        })
      ]),
      el("label", { class: "dev-hint", text: "Nahrať súbor:" }),
      fileInput,
      area
    ]);
  }

  // ---------- render ----------
  function renderBody() {
    const view =
      tab === "layers"
        ? renderLayers()
        : tab === "pick"
        ? renderPick()
        : tab === "palette"
        ? renderPalette()
        : tab === "trails"
        ? renderTrails()
        : tab === "shields"
        ? renderShields()
        : tab === "icons"
        ? renderIcons()
        : tab === "poi"
        ? renderPoi()
        : renderFile();
    const scroll = body.scrollTop;
    body.replaceChildren(view);
    body.scrollTop = scroll;
  }

  function render() {
    renderTabs();
    renderBody();
    renderStatus();
  }

  render();

  return {
    getOverrides: () => overrides,
    refresh: render,
    /** Beží inšpektor? Viewer vtedy nevyskakuje s vlastným popupom. */
    isPicking: () => tab === "pick",
    setOverrides(next) {
      overrides = normalizeOverrides(next).overrides;
      apply({ immediate: true });
    }
  };
}
