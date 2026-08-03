/**
 * Farebné témy a generátor MapLibre štýlu pre OpenMapTiles schému
 * (výstup Planetileru). Zdieľané medzi webom (prehliadač) a pipeline
 * (workers/build-styles.mjs generuje statické style.json aj pre iOS).
 *
 * Filozofia detailu:
 *   - do zoomu DETAIL_Z (14) sa mapa postupne "oreže" – nižšie zoomy
 *     ukazujú len dôležité prvky, aby bola čitateľná,
 *   - od DETAIL_Z vyššie sa nič nefiltruje: všetky body, línie aj plochy,
 *     ktoré OpenMapTiles schéma obsahuje, sú viditeľné,
 *   - dlaždice končia na zoome 16 (tvrdý limit Planetileru), vyššie zoomy
 *     (až po MAX_DISPLAY_Z = 20) rieši MapLibre overzoomom.
 */

/** Zoom, od ktorého je mapa plne detailná (nižšie sa orezáva). */
export const DETAIL_Z = 14;

/** Najvyšší zoom, na ktorý sa dá v mape priblížiť (overzoom nad dlaždicami). */
export const MAX_DISPLAY_Z = 20;

/** Najvyšší zoom dlaždíc, ktorý Planetiler dokáže vygenerovať. */
export const MAX_TILE_Z = 16;

/**
 * Zdroj výškových dát pre tieňovanie reliéfu (hillshade) a 3D terén.
 * OpenStreetMap terénny model neobsahuje – `ele` je len bodový tag na
 * vrcholoch a pod. Terén preto ide z AWS Terrain Tiles (Terrarium),
 * ktoré sú verejné a bez autentifikácie.
 */
export const DEFAULT_DEM_TILES =
  "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

export const THEMES = {
  svetla: {
    label: "Svetlá",
    background: "#f8f4f0",
    water: "#a4c8e8",
    waterOutline: "#88b0d8",
    river: "#a4c8e8",
    forest: "#c2dcb2",
    grass: "#d8e8c8",
    park: "#c9e8b8",
    parkOutline: "#a8cf90",
    sand: "#f0e6c8",
    ice: "#eef6fa",
    wetland: "#d6e3d0",
    rock: "#e6e0d8",
    residential: "#eae6e1",
    industrial: "#e4dce8",
    cemetery: "#d3e0d0",
    hospital: "#f6e0e0",
    school: "#f0e8d8",
    military: "#eee0d8",
    quarry: "#ddd6cc",
    pitch: "#cfe6bd",
    garden: "#d4ecc0",
    playground: "#dff0d8",
    building: "#d9cfc5",
    buildingOutline: "#c4b8ac",
    buildingTop: "#e2d8ce",
    aeroway: "#e8e0e8",
    aerowayLine: "#ffffff",
    motorway: "#f0a862",
    motorwayCasing: "#d88a3c",
    trunk: "#f5c26b",
    primary: "#fcd6a4",
    secondary: "#f7ecc8",
    minor: "#ffffff",
    service: "#ffffff",
    pedestrian: "#f2efe9",
    roadCasing: "#c8bda8",
    path: "#b08858",
    footway: "#c08858",
    cycleway: "#6a8fd0",
    steps: "#c05a3a",
    track: "#b09060",
    rail: "#9a9a9a",
    railHatch: "#ffffff",
    ferry: "#8aa8c8",
    aerialway: "#8a8a8a",
    pier: "#e8e4dc",
    boundary: "#9e7bb5",
    boundaryLocal: "#b8a0c8",
    placeText: "#333333",
    placeHalo: "#ffffff",
    roadText: "#5a4a3a",
    waterText: "#4a7bab",
    poiText: "#666655",
    poiHalo: "#ffffff",
    peakText: "#6a5a3a",
    houseText: "#a09488",
    contour: "#b09070",
    contourMajor: "#96764e",
    contourText: "#8a6a45",
    hillShadow: "#5a4a3a",
    hillHighlight: "#ffffff",
    hillAccent: "#8a7a6a"
  },
  tmava: {
    label: "Tmavá",
    background: "#14141f",
    water: "#16213e",
    waterOutline: "#1d2b52",
    river: "#16213e",
    forest: "#17251a",
    grass: "#1a281c",
    park: "#1b2e1e",
    parkOutline: "#2a4030",
    sand: "#2a2820",
    ice: "#1e2630",
    wetland: "#18241d",
    rock: "#26262e",
    residential: "#1b1b28",
    industrial: "#201b28",
    cemetery: "#1a231c",
    hospital: "#281b20",
    school: "#242015",
    military: "#2a1f1f",
    quarry: "#242028",
    pitch: "#1c2a1d",
    garden: "#1b2c1c",
    playground: "#1e2a20",
    building: "#262433",
    buildingOutline: "#33304a",
    buildingTop: "#302d40",
    aeroway: "#232030",
    aerowayLine: "#3a3750",
    motorway: "#b0763a",
    motorwayCasing: "#7a4e20",
    trunk: "#8a6a35",
    primary: "#6a5a35",
    secondary: "#4a4238",
    minor: "#33303f",
    service: "#2b2836",
    pedestrian: "#22222e",
    roadCasing: "#0e0e16",
    path: "#5a4a3a",
    footway: "#6a5540",
    cycleway: "#41618f",
    steps: "#7a4030",
    track: "#5a4a35",
    rail: "#3f3f52",
    railHatch: "#5a5a70",
    ferry: "#3a4a66",
    aerialway: "#55556a",
    pier: "#2a2833",
    boundary: "#7a5f95",
    boundaryLocal: "#5f4a75",
    placeText: "#c8c8d8",
    placeHalo: "#14141f",
    roadText: "#9a8f80",
    waterText: "#5a7bab",
    poiText: "#9a95a8",
    poiHalo: "#14141f",
    peakText: "#a8a08a",
    houseText: "#6a6678",
    contour: "#4a4436",
    contourMajor: "#6a6048",
    contourText: "#8a7f60",
    hillShadow: "#000000",
    hillHighlight: "#4a4a60",
    hillAccent: "#2a2a3a"
  },
  outdoor: {
    label: "Outdoor / Turistická",
    background: "#f4f1e4",
    water: "#8ec4dd",
    waterOutline: "#6faac6",
    river: "#7ab8d4",
    forest: "#a8cc8e",
    grass: "#cbe0aa",
    park: "#b8d898",
    parkOutline: "#88b070",
    sand: "#ecdfb5",
    ice: "#ffffff",
    wetland: "#bcd8c0",
    rock: "#d8d0c0",
    residential: "#e8e2d0",
    industrial: "#ddd6c4",
    cemetery: "#c5d4b5",
    hospital: "#eedcd0",
    school: "#e6dcc0",
    military: "#e2cfc4",
    quarry: "#cfc7b4",
    pitch: "#b9dd9a",
    garden: "#c4e2a8",
    playground: "#d2e8b8",
    building: "#c8b8a0",
    buildingOutline: "#a89880",
    buildingTop: "#d8c8b0",
    aeroway: "#ddd8cc",
    aerowayLine: "#f4f1e4",
    motorway: "#e88a4a",
    motorwayCasing: "#b5642a",
    trunk: "#eeaa55",
    primary: "#f2c96b",
    secondary: "#f5e9b8",
    minor: "#fdfaf2",
    service: "#fdfaf2",
    pedestrian: "#efe9da",
    roadCasing: "#a89878",
    path: "#c04030",
    footway: "#b03828",
    cycleway: "#3060b0",
    steps: "#a02818",
    track: "#96703c",
    rail: "#787868",
    railHatch: "#f4f1e4",
    ferry: "#5f93b5",
    aerialway: "#5a5a5a",
    pier: "#e0dac8",
    boundary: "#8a6aa0",
    boundaryLocal: "#a880b8",
    placeText: "#2a2a1a",
    placeHalo: "#f4f1e4",
    roadText: "#4a3a2a",
    waterText: "#33688a",
    poiText: "#4a5a3a",
    poiHalo: "#f4f1e4",
    peakText: "#5a3a20",
    houseText: "#8a7a60",
    contour: "#b3835a",
    contourMajor: "#966034",
    contourText: "#7a4f28",
    hillShadow: "#6a5030",
    hillHighlight: "#fffaf0",
    hillAccent: "#9a8060"
  },
  retro: {
    label: "Retro / Pastel",
    background: "#fdf6ec",
    water: "#b5d5c5",
    waterOutline: "#95bfa9",
    river: "#a5cbb8",
    forest: "#d5e3c0",
    grass: "#e5ecd0",
    park: "#dbe8c5",
    parkOutline: "#c0d8a8",
    sand: "#f5e8cc",
    ice: "#f2f5f0",
    wetland: "#cfe0d5",
    rock: "#e8ded0",
    residential: "#f7ecdd",
    industrial: "#efe2dc",
    cemetery: "#e0e5d2",
    hospital: "#f8e2dc",
    school: "#f5ead5",
    military: "#f0dcd4",
    quarry: "#e2d8cc",
    pitch: "#dcecc4",
    garden: "#e2eec8",
    playground: "#e8f0d4",
    building: "#e8c8b8",
    buildingOutline: "#d0a890",
    buildingTop: "#f0d8c8",
    aeroway: "#eee2d8",
    aerowayLine: "#fdf6ec",
    motorway: "#e08a7a",
    motorwayCasing: "#b8604e",
    trunk: "#eaa88a",
    primary: "#f2c8a0",
    secondary: "#f5e2c5",
    minor: "#fffdf8",
    service: "#fffdf8",
    pedestrian: "#f8f2e8",
    roadCasing: "#d5bfa5",
    path: "#b07858",
    footway: "#b07858",
    cycleway: "#7a9fb8",
    steps: "#b06048",
    track: "#b58e6a",
    rail: "#b0a598",
    railHatch: "#fdf6ec",
    ferry: "#8fb8a8",
    aerialway: "#a89c90",
    pier: "#f0e6da",
    boundary: "#c090a8",
    boundaryLocal: "#c8a0b8",
    placeText: "#5a4a45",
    placeHalo: "#fdf6ec",
    roadText: "#7a5a4a",
    waterText: "#4a8a7a",
    poiText: "#8a7060",
    poiHalo: "#fdf6ec",
    peakText: "#7a6250",
    houseText: "#b0a294",
    contour: "#c8a488",
    contourMajor: "#b0846a",
    contourText: "#9a7058",
    hillShadow: "#8a6a58",
    hillHighlight: "#fffdf8",
    hillAccent: "#c0a090"
  }
};

/**
 * Záložný zoznam ikon (bez prípony `_11`) pre prípad, že sa nepodarí načítať
 * sprite index. Ak sprite index k dispozícii je, zoznam sa berie z neho –
 * vďaka tomu nikdy neodkazujeme na ikonu, ktorá v sprite neexistuje
 * (chýbajúca ikona = symbol sa nevykreslí).
 */
const FALLBACK_ICONS = [
  "alcohol_shop", "art_gallery", "attraction", "bakery", "bank", "bar",
  "beer", "bicycle", "bus", "cafe", "campsite", "car", "castle", "cemetery",
  "cinema", "clothing_store", "college", "dentist", "doctors", "drinking_water",
  "fast_food", "fire_station", "fuel", "golf", "grocery", "harbor",
  "hospital", "ice_cream", "information", "library", "lodging", "monument",
  "museum", "park", "parking", "pharmacy", "picnic_site", "place_of_worship",
  "playground", "police", "post", "railway", "restaurant", "school", "shop",
  "stadium", "swimming", "theatre", "toilets", "town_hall", "veterinary", "zoo"
];

/** Ikony, ktoré má sprite osm-liberty pre `mountain_peak` a `aerodrome_label`. */
const PEAK_ICON = "mountain_11";
const VOLCANO_ICON = "volcano_11";
const AIRPORT_ICON = "airport_11";

const DEFAULT_FONTS = {
  regular: "Noto Sans Regular",
  bold: "Noto Sans Bold",
  italic: "Noto Sans Italic"
};

/**
 * Interpolácia šírky čiary podľa zoomu: zw([[z, w], …]).
 *
 * Pozn.: `["zoom"]` smie byť podľa MapLibre style-spec iba priamym vstupom
 * najvrchnejšieho `interpolate`/`step`. Preto sa šírky obrysov (casing)
 * počítajú pripočítaním k jednotlivým stopom (`widen`), nie výrazom `["+", …]`.
 */
const zw = (stops) => [
  "interpolate",
  ["exponential", 1.5],
  ["zoom"],
  ...stops.flat()
];

/** Lineárna interpolácia podľa zoomu. */
const zl = (stops) => ["interpolate", ["linear"], ["zoom"], ...stops.flat()];

/** Rozšíri každý stop o konštantu – použité na obrysy ciest. */
const widen = (stops, extra) => stops.map(([z, w]) => [z, w + extra]);

/** Bezpečné čítanie atribútu pre `in`/porovnania (chýbajúci atribút = ""). */
const str = (prop) => ["coalesce", ["get", prop], ""];
const num = (prop, fallback) => ["coalesce", ["get", prop], fallback];

const isTunnel = ["==", ["get", "brunnel"], "tunnel"];
const isBridge = ["==", ["get", "brunnel"], "bridge"];
const isSurface = ["all", ["!=", ["get", "brunnel"], "tunnel"], ["!=", ["get", "brunnel"], "bridge"]];

/**
 * Vygeneruje kompletný MapLibre GL štýl.
 *
 * @param {object} opts
 * @param {string} opts.theme       kľúč témy z THEMES
 * @param {string} opts.tilesUrl    napr. "pmtiles://https://…/tiles/slovensko.pmtiles"
 * @param {string} opts.spriteUrl   absolútna URL spritu (bez prípony)
 * @param {string} opts.glyphsUrl   URL šablóna glyfov {fontstack}/{range}
 * @param {string} [opts.name]      názov štýlu
 * @param {string[]} [opts.icons]   mená ikon dostupných v sprite (s `_11` aj bez)
 * @param {object} [opts.fonts]     {regular, bold, italic} – názvy fontstackov
 * @param {number} [opts.maxzoom]   najvyšší zoom dlaždíc (default MAX_TILE_Z)
 * @param {string} [opts.contoursUrl]     pmtiles:// URL s vrstevnicami (voliteľné)
 * @param {number} [opts.contoursMaxzoom] najvyšší zoom dlaždíc s vrstevnicami
 * @param {string|null} [opts.demTiles]   raster-dem dlaždice pre hillshade
 *                                        (null = bez tieňovania reliéfu)
 */
export function buildStyle({
  theme,
  tilesUrl,
  spriteUrl,
  glyphsUrl,
  name,
  icons,
  fonts,
  maxzoom = MAX_TILE_Z,
  contoursUrl = null,
  contoursMaxzoom = 14,
  demTiles = DEFAULT_DEM_TILES
}) {
  const c = THEMES[theme];
  if (!c) throw new Error(`Neznáma téma: ${theme}`);

  const f = { ...DEFAULT_FONTS, ...(fonts || {}) };
  const REG = [f.regular];
  const BOLD = [f.bold];
  const ITAL = [f.italic];

  // Z mien v sprite spravíme zoznam tried, pre ktoré existuje ikona `<trieda>_11`.
  const iconClasses = icons && icons.length
    ? [...new Set(icons.filter((n) => n.endsWith("_11")).map((n) => n.slice(0, -3)))]
    : FALLBACK_ICONS;
  const hasIcon = (n) => (icons && icons.length ? icons.includes(n) : true);

  const nameExpr = [
    "coalesce",
    ["get", "name:sk"],
    ["get", "name"],
    ["get", "name:latin"],
    ""
  ];

  const style = {
    version: 8,
    name: name || `FricoMaps – ${c.label}`,
    sources: {
      omt: {
        type: "vector",
        url: tilesUrl,
        // Dlaždice končia na `maxzoom`; vyššie zoomy MapLibre dopočíta
        // overzoomom, takže mapa je použiteľná až po MAX_DISPLAY_Z.
        maxzoom,
        attribution:
          '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> prispievatelia'
      }
    },
    sprite: spriteUrl,
    glyphs: glyphsUrl,
    layers: []
  };

  // Vrstevnice sú samostatný .pmtiles – nezávislý od OSM buildu, lebo
  // závisia len od územia, nie od toho, čo sa v OSM zmenilo.
  if (contoursUrl) {
    style.sources.contours = {
      type: "vector",
      url: contoursUrl,
      maxzoom: contoursMaxzoom,
      attribution:
        '<a href="https://spacedata.copernicus.eu/">Copernicus DEM</a>'
    };
  }
  // Raster DEM pre tieňovanie reliéfu a 3D terén (funguje na webe aj iOS).
  if (demTiles) {
    style.sources.dem = {
      type: "raster-dem",
      tiles: [demTiles],
      encoding: "terrarium",
      tileSize: 256,
      maxzoom: 15,
      attribution:
        '<a href="https://registry.opendata.aws/terrain-tiles/">AWS Terrain Tiles</a>'
    };
  }

  const L = style.layers;
  const add = (layer) => L.push({ source: "omt", ...layer });

  L.push({
    id: "background",
    type: "background",
    paint: { "background-color": c.background }
  });

  // ================= krajinná pokrývka =================
  const landcover = [
    ["wood", ["wood", "forest"], c.forest, 0.9],
    ["grass", ["grass", "grassland", "meadow"], c.grass, 0.7],
    ["farmland", ["farmland"], c.grass, 0.45],
    ["wetland", ["wetland", "swamp", "marsh", "bog"], c.wetland, 0.8],
    ["rock", ["rock", "scree", "bare_rock"], c.rock, 0.8],
    ["sand", ["sand", "beach"], c.sand, 1],
    ["ice", ["ice", "glacier"], c.ice, 1]
  ];
  for (const [id, classes, color, opacity] of landcover) {
    add({
      id: `landcover-${id}`,
      type: "fill",
      "source-layer": "landcover",
      filter: [
        "any",
        ["in", str("class"), ["literal", classes]],
        ["in", str("subclass"), ["literal", classes]]
      ],
      paint: { "fill-color": color, "fill-opacity": opacity }
    });
  }

  // ================= využitie územia =================
  const landuse = [
    ["residential", ["residential", "suburb", "neighbourhood", "quarter"], c.residential],
    ["industrial", ["industrial", "commercial", "retail", "garages", "warehouse"], c.industrial],
    ["railway", ["railway", "bus_station"], c.industrial],
    ["cemetery", ["cemetery", "grave_yard"], c.cemetery],
    ["hospital", ["hospital"], c.hospital],
    ["school", ["school", "university", "college", "kindergarten", "library"], c.school],
    ["military", ["military", "danger_area"], c.military],
    ["quarry", ["quarry", "landfill"], c.quarry],
    ["garden", ["garden", "allotments", "orchard", "vineyard"], c.garden],
    ["playground", ["playground", "theme_park", "zoo"], c.playground],
    ["pitch", ["pitch", "stadium", "track", "sports_centre", "golf_course"], c.pitch]
  ];
  for (const [id, classes, color] of landuse) {
    add({
      id: `landuse-${id}`,
      type: "fill",
      "source-layer": "landuse",
      filter: ["in", str("class"), ["literal", classes]],
      paint: { "fill-color": color }
    });
  }

  add({
    id: "park",
    type: "fill",
    "source-layer": "park",
    paint: { "fill-color": c.park, "fill-opacity": 0.55 }
  });
  add({
    id: "park-outline",
    type: "line",
    "source-layer": "park",
    minzoom: 10,
    paint: {
      "line-color": c.parkOutline,
      "line-width": zl([[10, 0.6], [16, 1.6], [20, 3]]),
      "line-dasharray": [4, 2]
    }
  });

  // ================= tieňovanie reliéfu =================
  // Ide nad krajinnú pokrývku, ale pod vodu – tieňovaná vodná hladina
  // vyzerá nesprávne.
  if (demTiles) {
    L.push({
      id: "hillshade",
      type: "hillshade",
      source: "dem",
      paint: {
        "hillshade-exaggeration": zl([[6, 0.5], [12, 0.4], [16, 0.25]]),
        "hillshade-shadow-color": c.hillShadow,
        "hillshade-highlight-color": c.hillHighlight,
        "hillshade-accent-color": c.hillAccent
      }
    });
  }

  // ================= voda =================
  add({
    id: "water",
    type: "fill",
    "source-layer": "water",
    filter: ["!=", ["get", "brunnel"], "tunnel"],
    paint: {
      "fill-color": c.water,
      "fill-opacity": ["case", ["==", ["get", "intermittent"], 1], 0.6, 1]
    }
  });
  add({
    id: "water-outline",
    type: "line",
    "source-layer": "water",
    minzoom: 12,
    filter: ["!=", ["get", "brunnel"], "tunnel"],
    paint: {
      "line-color": c.waterOutline,
      "line-width": zl([[12, 0.4], [16, 1.2], [20, 2.5]])
    }
  });
  add({
    id: "waterway-river",
    type: "line",
    "source-layer": "waterway",
    filter: ["in", str("class"), ["literal", ["river", "canal"]]],
    paint: {
      "line-color": c.river,
      "line-width": zw([[8, 0.6], [12, 1.6], [16, 5], [20, 18]])
    }
  });
  add({
    // Potoky, priekopy, odvodňovacie kanály – detail, ktorý sa objaví od z12.
    id: "waterway-minor",
    type: "line",
    "source-layer": "waterway",
    minzoom: 12,
    filter: ["in", str("class"), ["literal", ["stream", "ditch", "drain"]]],
    paint: {
      "line-color": c.river,
      "line-width": zw([[12, 0.5], [16, 2], [20, 7]]),
      "line-opacity": 0.85
    }
  });

  // ================= vrstevnice =================
  // Kreslia sa nad vodou (pod hladinou nemajú čo robiť) a pod budovami
  // a cestami, aby neprekrývali dôležitejšie prvky.
  if (contoursUrl) {
    const contourLine = (id, level, minzoom, width, color) =>
      L.push({
        id: `contour-${id}`,
        type: "line",
        source: "contours",
        "source-layer": "contour",
        minzoom,
        filter: ["==", str("level"), level],
        paint: {
          "line-color": color,
          "line-width": zl(width),
          "line-opacity": zl([[minzoom, 0], [minzoom + 1, 0.55]])
        }
      });

    contourLine("minor", "minor", 13, [[13, 0.4], [16, 0.7], [20, 1.4]], c.contour);
    contourLine("mid", "mid", 12, [[12, 0.5], [16, 0.9], [20, 1.8]], c.contour);
    contourLine("major", "major", 10, [[10, 0.7], [16, 1.4], [20, 2.6]], c.contourMajor);

    // Popisky nadmorskej výšky pozdĺž hlavných vrstevníc.
    L.push({
      id: "contour-label",
      type: "symbol",
      source: "contours",
      "source-layer": "contour",
      minzoom: 13,
      filter: ["in", str("level"), ["literal", ["major", "mid"]]],
      layout: {
        "symbol-placement": "line",
        // `ele` môže z GDALu prísť ako desatinné číslo – zaokrúhlime v štýle,
        // aby popisok nikdy nebol "810.0 m".
        "text-field": ["concat", ["to-string", ["round", num("ele", 0)]], " m"],
        "text-font": REG,
        "text-size": zl([[13, 9], [16, 11], [20, 13]]),
        "symbol-spacing": 320,
        "text-max-angle": 25,
        "text-padding": 8
      },
      paint: {
        "text-color": c.contourText,
        "text-halo-color": c.poiHalo,
        "text-halo-width": 1.4
      }
    });
  }

  // ================= letiská =================
  add({
    id: "aeroway-area",
    type: "fill",
    "source-layer": "aeroway",
    filter: ["in", str("class"), ["literal", ["apron", "aerodrome", "heliport"]]],
    paint: { "fill-color": c.aeroway }
  });
  add({
    id: "aeroway-runway",
    type: "line",
    "source-layer": "aeroway",
    minzoom: 10,
    filter: ["==", ["get", "class"], "runway"],
    paint: {
      "line-color": c.aeroway,
      "line-width": zw([[10, 1], [14, 8], [16, 20], [20, 70]])
    }
  });
  add({
    id: "aeroway-taxiway",
    type: "line",
    "source-layer": "aeroway",
    minzoom: 11,
    filter: ["in", str("class"), ["literal", ["taxiway", "helipad"]]],
    paint: {
      "line-color": c.aeroway,
      "line-width": zw([[11, 0.6], [14, 3], [16, 8], [20, 26]])
    }
  });

  // ================= budovy =================
  // Do z15.5 ploché výplne, nad tým 3D bloky (render_height z OSM).
  add({
    id: "building",
    type: "fill",
    "source-layer": "building",
    minzoom: 13,
    maxzoom: 16,
    paint: {
      "fill-color": c.building,
      "fill-outline-color": c.buildingOutline,
      "fill-opacity": zl([[13, 0.5], [15, 1]])
    }
  });
  add({
    id: "building-3d",
    type: "fill-extrusion",
    "source-layer": "building",
    minzoom: 16,
    filter: ["!=", ["get", "hide_3d"], true],
    paint: {
      "fill-extrusion-color": c.buildingTop,
      "fill-extrusion-opacity": 0.9,
      "fill-extrusion-height": num("render_height", 5),
      "fill-extrusion-base": num("render_min_height", 0)
    }
  });

  // ================= doprava =================
  // Šírky sú definované až po z20, aby overzoomované dlaždice vyzerali správne.
  // [id, triedy, farba výplne, farba obrysu, stopy šírky, prídavok obrysu, minzoom]
  const roadDefs = [
    ["motorway", ["motorway"], c.motorway, c.motorwayCasing,
      [[5, 0.8], [10, 3], [14, 8], [16, 18], [20, 60]], 3, 4],
    ["trunk", ["trunk"], c.trunk, c.roadCasing,
      [[6, 0.7], [10, 2.6], [14, 7], [16, 16], [20, 52]], 2.6, 6],
    ["primary", ["primary"], c.primary, c.roadCasing,
      [[7, 0.7], [10, 2.2], [14, 6.5], [16, 15], [20, 48]], 2.4, 7],
    ["secondary", ["secondary"], c.secondary, c.roadCasing,
      [[9, 0.6], [12, 2], [14, 5], [16, 12], [20, 40]], 2, 9],
    ["tertiary", ["tertiary"], c.secondary, c.roadCasing,
      [[10, 0.5], [12, 1.6], [14, 4.2], [16, 10], [20, 34]], 1.8, 10],
    ["minor", ["minor", "living_street", "raceway", "busway", "bus_guideway"], c.minor, c.roadCasing,
      [[12, 0.6], [14, 3.5], [16, 9], [20, 32]], 1.6, 12],
    ["service", ["service"], c.service, c.roadCasing,
      [[13, 0.5], [14, 2], [16, 6], [20, 22]], 1.2, 13],
    ["pedestrian", ["pedestrian"], c.pedestrian, c.roadCasing,
      [[13, 0.6], [14, 2.4], [16, 7], [20, 24]], 1.2, 13]
  ];

  /** Cesty sa kreslia v troch priechodoch: tunely → povrch → mosty. */
  const roadPass = (suffix, extraFilter, opts = {}) => {
    const layout = { "line-cap": opts.cap || "round", "line-join": "round" };
    const filterFor = (classes) => [
      "all",
      ["in", str("class"), ["literal", classes]],
      extraFilter
    ];
    // obrysy (casing) idú celé pod výplne, inak by ich prekrývali križovatky
    for (const [id, classes, , casingColor, stops, extra, mz] of roadDefs) {
      add({
        id: `road-${id}-casing${suffix}`,
        type: "line",
        "source-layer": "transportation",
        minzoom: mz,
        filter: filterFor(classes),
        layout,
        paint: {
          "line-color": casingColor,
          "line-width": zw(widen(stops, extra)),
          ...(opts.dash ? { "line-dasharray": opts.dash } : {})
        }
      });
    }
    for (const [id, classes, color, , stops, , mz] of roadDefs) {
      add({
        id: `road-${id}${suffix}`,
        type: "line",
        "source-layer": "transportation",
        minzoom: mz,
        filter: filterFor(classes),
        layout,
        paint: { "line-color": color, "line-width": zw(stops) }
      });
    }
  };

  // --- tunely (prerušované, pod povrchom) ---
  roadPass("-tunnel", isTunnel, { cap: "butt", dash: [3, 2] });

  // --- chodníky, cyklotrasy, schody, poľné cesty ---
  const pathDefs = [
    ["track", ["==", str("class"), "track"], c.track, [[12, 0.7], [14, 1.6], [16, 3.5], [20, 12]], [4, 2], 12],
    ["steps", ["==", str("subclass"), "steps"], c.steps, [[14, 1.2], [16, 3], [20, 10]], [1, 0.6], 14],
    ["cycleway", ["==", str("subclass"), "cycleway"], c.cycleway, [[13, 0.7], [16, 2.2], [20, 8]], [3, 1.5], 13],
    ["footway", ["in", str("subclass"), ["literal", ["footway", "sidewalk", "crossing"]]], c.footway, [[13, 0.6], [16, 2], [20, 7]], [2, 1.5], 13],
    // `path` bez subclass (alebo path/bridleway) – ale nie `track`, ten má vlastnú vrstvu
    ["path", ["all", ["==", str("class"), "path"],
      ["in", str("subclass"), ["literal", ["path", "bridleway", ""]]]],
      c.path, [[12, 0.7], [16, 2.2], [20, 8]], [2, 2], 12]
  ];
  for (const [id, filter, color, stops, dash, mz] of pathDefs) {
    add({
      id: `road-${id}`,
      type: "line",
      "source-layer": "transportation",
      minzoom: mz,
      filter: [
        "all",
        ["in", str("class"), ["literal", ["path", "track"]]],
        filter,
        ["!=", ["get", "brunnel"], "tunnel"]
      ],
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": color, "line-width": zw(stops), "line-dasharray": dash }
    });
  }

  // --- povrchové cesty ---
  roadPass("", isSurface);

  // --- železnica ---
  add({
    id: "rail-bg",
    type: "line",
    "source-layer": "transportation",
    minzoom: 9,
    filter: ["in", str("class"), ["literal", ["rail", "transit"]]],
    paint: {
      "line-color": c.rail,
      "line-width": zw([[9, 0.8], [14, 2.4], [16, 4], [20, 12]])
    }
  });
  add({
    id: "rail-hatch",
    type: "line",
    "source-layer": "transportation",
    minzoom: 13,
    filter: ["in", str("class"), ["literal", ["rail", "transit"]]],
    paint: {
      "line-color": c.railHatch,
      "line-width": zw([[13, 0.8], [16, 2], [20, 6]]),
      "line-dasharray": [0.3, 2.5]
    }
  });

  // --- mosty (nad všetkým ostatným) ---
  roadPass("-bridge", isBridge, { cap: "butt" });

  // --- lanovky, kompy, móla ---
  add({
    id: "aerialway",
    type: "line",
    "source-layer": "transportation",
    minzoom: 11,
    filter: ["==", ["get", "class"], "aerialway"],
    paint: {
      "line-color": c.aerialway,
      "line-width": zl([[11, 0.6], [16, 1.6], [20, 3]]),
      "line-dasharray": [6, 2]
    }
  });
  add({
    id: "ferry",
    type: "line",
    "source-layer": "transportation",
    minzoom: 8,
    filter: ["==", ["get", "class"], "ferry"],
    paint: {
      "line-color": c.ferry,
      "line-width": zl([[8, 0.8], [16, 2], [20, 4]]),
      "line-dasharray": [4, 3]
    }
  });
  add({
    id: "pier",
    type: "line",
    "source-layer": "transportation",
    minzoom: 13,
    filter: ["==", ["get", "class"], "pier"],
    paint: {
      "line-color": c.pier,
      "line-width": zw([[13, 1], [16, 4], [20, 14]])
    }
  });

  // --- jednosmerky (len na veľkom detaile) ---
  if (hasIcon("arrow")) {
    add({
      id: "road-oneway",
      type: "symbol",
      "source-layer": "transportation",
      minzoom: 16,
      filter: ["in", num("oneway", 0), ["literal", [1, -1]]],
      layout: {
        "symbol-placement": "line",
        "symbol-spacing": 120,
        "icon-image": "arrow",
        "icon-size": zl([[16, 0.6], [20, 1.2]]),
        "icon-rotate": ["case", ["==", num("oneway", 0), -1], 180, 0],
        "icon-rotation-alignment": "map",
        "icon-allow-overlap": true
      },
      paint: { "icon-opacity": 0.5 }
    });
  }

  // ================= hranice =================
  add({
    id: "boundary-municipality",
    type: "line",
    "source-layer": "boundary",
    minzoom: 11,
    filter: [">=", num("admin_level", 99), 7],
    paint: {
      "line-color": c.boundaryLocal,
      "line-width": zl([[11, 0.5], [16, 1.2], [20, 2]]),
      "line-dasharray": [2, 2],
      "line-opacity": 0.5
    }
  });
  add({
    id: "boundary-district",
    type: "line",
    "source-layer": "boundary",
    minzoom: 8,
    filter: ["all", [">=", num("admin_level", 99), 5], ["<=", num("admin_level", 99), 6]],
    paint: {
      "line-color": c.boundaryLocal,
      "line-width": zl([[8, 0.6], [16, 1.6], [20, 3]]),
      "line-dasharray": [3, 2],
      "line-opacity": 0.6
    }
  });
  add({
    id: "boundary-region",
    type: "line",
    "source-layer": "boundary",
    filter: ["all", [">=", num("admin_level", 99), 3], ["<=", num("admin_level", 99), 4]],
    paint: {
      "line-color": c.boundary,
      "line-width": zl([[4, 0.8], [12, 2], [20, 4]]),
      "line-dasharray": [3, 2],
      "line-opacity": 0.7
    }
  });
  add({
    id: "boundary-country",
    type: "line",
    "source-layer": "boundary",
    filter: ["<=", num("admin_level", 99), 2],
    paint: {
      "line-color": c.boundary,
      "line-width": zl([[4, 1], [12, 3], [20, 6]])
    }
  });

  // ================= popisky =================
  add({
    id: "waterway-name",
    type: "symbol",
    "source-layer": "waterway",
    minzoom: 13,
    layout: {
      "symbol-placement": "line",
      "text-field": nameExpr,
      "text-font": ITAL,
      "text-size": zl([[13, 10], [18, 13]])
    },
    paint: {
      "text-color": c.waterText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1
    }
  });
  add({
    id: "water-name",
    type: "symbol",
    "source-layer": "water_name",
    layout: {
      "text-field": nameExpr,
      "text-font": ITAL,
      "text-size": zl([[8, 10], [16, 14]]),
      "text-max-width": 8
    },
    paint: {
      "text-color": c.waterText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1
    }
  });

  add({
    id: "park-name",
    type: "symbol",
    "source-layer": "park",
    minzoom: 11,
    layout: {
      "text-field": nameExpr,
      "text-font": REG,
      "text-size": zl([[11, 10], [16, 13]]),
      "text-max-width": 8
    },
    paint: {
      "text-color": c.poiText,
      "text-halo-color": c.poiHalo,
      "text-halo-width": 1.2
    }
  });

  add({
    id: "road-name",
    type: "symbol",
    "source-layer": "transportation_name",
    minzoom: 13,
    layout: {
      "symbol-placement": "line",
      "text-field": nameExpr,
      "text-font": REG,
      "text-size": zl([[13, 10], [16, 12], [20, 16]])
    },
    paint: {
      "text-color": c.roadText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1.2
    }
  });

  // Súpisné/orientačné čísla – iba na najväčšom detaile.
  add({
    id: "housenumber",
    type: "symbol",
    "source-layer": "housenumber",
    minzoom: 17,
    layout: {
      "text-field": ["get", "housenumber"],
      "text-font": REG,
      "text-size": zl([[17, 9], [20, 12]]),
      "text-allow-overlap": false
    },
    paint: {
      "text-color": c.houseText,
      "text-halo-color": c.poiHalo,
      "text-halo-width": 1
    }
  });

  // ---- POI ----
  // Ikona sa vyberá podľa `subclass`, potom `class`; ak pre ne sprite ikonu
  // nemá, použije sa bodka – nikdy neodkazujeme na neexistujúcu ikonu.
  const iconExpr = [
    "case",
    ["in", str("subclass"), ["literal", iconClasses]],
    ["concat", str("subclass"), "_11"],
    ["in", str("class"), ["literal", iconClasses]],
    ["concat", str("class"), "_11"],
    hasIcon("circle_11") ? "circle_11" : "dot_11"
  ];

  const poiLayout = {
    "icon-image": iconExpr,
    "icon-size": zl([[14, 0.9], [18, 1.1], [20, 1.3]]),
    "icon-optional": true,
    "text-field": nameExpr,
    "text-font": REG,
    "text-size": zl([[14, 10], [18, 12], [20, 14]]),
    "text-offset": [0, 1.1],
    "text-anchor": "top",
    "text-optional": true,
    "text-max-width": 9,
    "symbol-sort-key": num("rank", 100)
  };
  const poiPaint = {
    "text-color": c.poiText,
    "text-halo-color": c.poiHalo,
    "text-halo-width": 1.2
  };

  // z14–16: len dôležitejšie POI, aby mapa nebola zahltená.
  add({
    id: "poi-major",
    type: "symbol",
    "source-layer": "poi",
    minzoom: DETAIL_Z,
    maxzoom: 16,
    filter: ["<=", num("rank", 100), 24],
    layout: poiLayout,
    paint: poiPaint
  });
  // z16+: úplne všetko, bez filtra na rank.
  add({
    id: "poi-all",
    type: "symbol",
    "source-layer": "poi",
    minzoom: 16,
    layout: { ...poiLayout, "text-allow-overlap": false },
    paint: poiPaint
  });

  // ---- vrcholy hôr (dôležité pre outdoor mapu) ----
  add({
    id: "mountain-peak",
    type: "symbol",
    "source-layer": "mountain_peak",
    minzoom: 9,
    layout: {
      "icon-image": [
        "case",
        ["==", ["get", "class"], "volcano"],
        hasIcon(VOLCANO_ICON) ? VOLCANO_ICON : PEAK_ICON,
        PEAK_ICON
      ],
      "icon-size": 0.9,
      "icon-optional": true,
      "text-field": [
        "case",
        ["has", "ele"],
        ["concat", nameExpr, "\n", ["to-string", ["get", "ele"]], " m"],
        nameExpr
      ],
      "text-font": REG,
      "text-size": zl([[9, 9], [14, 11], [20, 14]]),
      "text-offset": [0, 0.9],
      "text-anchor": "top",
      "text-optional": true,
      "symbol-sort-key": num("rank", 100)
    },
    paint: {
      "text-color": c.peakText,
      "text-halo-color": c.poiHalo,
      "text-halo-width": 1.4
    }
  });

  add({
    id: "aerodrome-label",
    type: "symbol",
    "source-layer": "aerodrome_label",
    minzoom: 10,
    layout: {
      ...(hasIcon(AIRPORT_ICON) ? { "icon-image": AIRPORT_ICON, "icon-size": 1 } : {}),
      "icon-optional": true,
      "text-field": nameExpr,
      "text-font": REG,
      "text-size": zl([[10, 10], [16, 13]]),
      "text-offset": [0, 1],
      "text-anchor": "top",
      "text-optional": true
    },
    paint: {
      "text-color": c.poiText,
      "text-halo-color": c.poiHalo,
      "text-halo-width": 1.2
    }
  });

  // ---- sídla ----
  const places = [
    ["neighbourhood", ["neighbourhood", "isolated_dwelling", "farm", "quarter"], 13, REG, zl([[13, 9], [18, 13]])],
    ["hamlet", ["hamlet"], 12, REG, zl([[12, 10], [18, 14]])],
    ["suburb", ["suburb"], 11, REG, zl([[11, 11], [18, 15]])],
    ["village", ["village"], 9, REG, zl([[9, 10], [16, 15]])],
    ["town", ["town"], 7, REG, zl([[7, 11], [14, 18]])],
    ["city", ["city"], 4, BOLD, zl([[4, 12], [13, 22]])],
    ["state", ["state", "province"], 4, BOLD, zl([[4, 11], [8, 15]])],
    ["country", ["country"], 2, BOLD, zl([[2, 11], [6, 18]])]
  ];
  for (const [id, classes, mz, font, size] of places) {
    add({
      id: `place-${id}`,
      type: "symbol",
      "source-layer": "place",
      minzoom: mz,
      filter: ["in", str("class"), ["literal", classes]],
      layout: {
        "text-field": nameExpr,
        "text-font": font,
        "text-size": size,
        "text-max-width": 9,
        "symbol-sort-key": num("rank", 100)
      },
      paint: {
        "text-color": c.placeText,
        "text-halo-color": c.placeHalo,
        "text-halo-width": 1.6
      }
    });
  }

  return style;
}

/** Vrstvy, na ktoré sa dá kliknúť (popup s detailom). */
export const CLICKABLE_LAYERS = ["poi-major", "poi-all", "mountain-peak", "aerodrome-label"];
