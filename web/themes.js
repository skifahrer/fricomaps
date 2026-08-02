/**
 * Farebné témy a generátor MapLibre štýlu pre OpenMapTiles schému
 * (výstup Planetileru). Zdieľané medzi webom (prehliadač) a pipeline
 * (tools/build-styles.mjs generuje statické style.json aj pre iOS).
 */

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
    sand: "#f0e6c8",
    ice: "#eef6fa",
    residential: "#eae6e1",
    industrial: "#e4dce8",
    cemetery: "#d3e0d0",
    hospital: "#f6e0e0",
    school: "#f0e8d8",
    building: "#d9cfc5",
    buildingOutline: "#c4b8ac",
    aeroway: "#e8e0e8",
    motorway: "#f0a862",
    motorwayCasing: "#d88a3c",
    trunk: "#f5c26b",
    primary: "#fcd6a4",
    secondary: "#f7ecc8",
    minor: "#ffffff",
    roadCasing: "#c8bda8",
    path: "#b08858",
    rail: "#9a9a9a",
    boundary: "#9e7bb5",
    placeText: "#333333",
    placeHalo: "#ffffff",
    roadText: "#5a4a3a",
    waterText: "#4a7bab",
    poiText: "#666655",
    poiHalo: "#ffffff"
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
    sand: "#2a2820",
    ice: "#1e2630",
    residential: "#1b1b28",
    industrial: "#201b28",
    cemetery: "#1a231c",
    hospital: "#281b20",
    school: "#242015",
    building: "#262433",
    buildingOutline: "#33304a",
    aeroway: "#232030",
    motorway: "#b0763a",
    motorwayCasing: "#7a4e20",
    trunk: "#8a6a35",
    primary: "#6a5a35",
    secondary: "#4a4238",
    minor: "#33303f",
    roadCasing: "#0e0e16",
    path: "#5a4a3a",
    rail: "#3f3f52",
    boundary: "#7a5f95",
    placeText: "#c8c8d8",
    placeHalo: "#14141f",
    roadText: "#9a8f80",
    waterText: "#5a7bab",
    poiText: "#9a95a8",
    poiHalo: "#14141f"
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
    sand: "#ecdfb5",
    ice: "#ffffff",
    residential: "#e8e2d0",
    industrial: "#ddd6c4",
    cemetery: "#c5d4b5",
    hospital: "#eedcd0",
    school: "#e6dcc0",
    building: "#c8b8a0",
    buildingOutline: "#a89880",
    aeroway: "#ddd8cc",
    motorway: "#e88a4a",
    motorwayCasing: "#b5642a",
    trunk: "#eeaa55",
    primary: "#f2c96b",
    secondary: "#f5e9b8",
    minor: "#fdfaf2",
    roadCasing: "#a89878",
    path: "#c04030",
    rail: "#787868",
    boundary: "#8a6aa0",
    placeText: "#2a2a1a",
    placeHalo: "#f4f1e4",
    roadText: "#4a3a2a",
    waterText: "#33688a",
    poiText: "#4a5a3a",
    poiHalo: "#f4f1e4"
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
    sand: "#f5e8cc",
    ice: "#f2f5f0",
    residential: "#f7ecdd",
    industrial: "#efe2dc",
    cemetery: "#e0e5d2",
    hospital: "#f8e2dc",
    school: "#f5ead5",
    building: "#e8c8b8",
    buildingOutline: "#d0a890",
    aeroway: "#eee2d8",
    motorway: "#e08a7a",
    motorwayCasing: "#b8604e",
    trunk: "#eaa88a",
    primary: "#f2c8a0",
    secondary: "#f5e2c5",
    minor: "#fffdf8",
    roadCasing: "#d5bfa5",
    path: "#b07858",
    rail: "#b0a598",
    boundary: "#c090a8",
    placeText: "#5a4a45",
    placeHalo: "#fdf6ec",
    roadText: "#7a5a4a",
    waterText: "#4a8a7a",
    poiText: "#8a7060",
    poiHalo: "#fdf6ec"
  }
};

/** Mapovanie POI tried (OpenMapTiles `poi.class`) na ikonky z osm-liberty spritu (maki). */
const POI_ICONS = [
  "alcohol_shop", "art_gallery", "attraction", "bakery", "bank", "bar",
  "beer", "bicycle", "bus", "cafe", "campsite", "castle", "cemetery",
  "cinema", "clothing_store", "college", "dentist", "doctors", "drinking_water",
  "fast_food", "fire_station", "fuel", "golf", "grocery", "harbor",
  "hospital", "ice_cream", "information", "library", "lodging", "monument",
  "museum", "park", "parking", "pharmacy", "picnic_site", "playground",
  "police", "post", "railway", "restaurant", "school", "shop", "stadium",
  "swimming", "theatre", "town_hall", "veterinary", "zoo"
];

/**
 * Vygeneruje kompletný MapLibre GL štýl.
 * @param {object} opts
 * @param {string} opts.theme      kľúč témy z THEMES
 * @param {string} opts.tilesUrl   napr. "pmtiles://https://…/tiles/slovensko.pmtiles"
 * @param {string} opts.spriteUrl  absolútna URL spritu (bez prípony)
 * @param {string} opts.glyphsUrl  URL šablóna glyfov {fontstack}/{range}
 * @param {string} [opts.name]     názov štýlu
 */
export function buildStyle({ theme, tilesUrl, spriteUrl, glyphsUrl, name }) {
  const c = THEMES[theme];
  if (!c) throw new Error(`Neznáma téma: ${theme}`);

  const style = {
    version: 8,
    name: name || `FricoMaps – ${c.label}`,
    sources: {
      omt: {
        type: "vector",
        url: tilesUrl,
        attribution:
          '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> prispievatelia'
      }
    },
    sprite: spriteUrl,
    glyphs: glyphsUrl,
    layers: []
  };

  const L = style.layers;

  L.push({
    id: "background",
    type: "background",
    paint: { "background-color": c.background }
  });

  // ---------- krajinná pokrývka / využitie ----------
  L.push({
    id: "landcover-wood",
    type: "fill",
    source: "omt",
    "source-layer": "landcover",
    filter: ["in", ["get", "class"], ["literal", ["wood", "forest"]]],
    paint: { "fill-color": c.forest, "fill-opacity": 0.85 }
  });
  L.push({
    id: "landcover-grass",
    type: "fill",
    source: "omt",
    "source-layer": "landcover",
    filter: ["in", ["get", "class"], ["literal", ["grass", "grassland", "meadow", "farmland"]]],
    paint: { "fill-color": c.grass, "fill-opacity": 0.6 }
  });
  L.push({
    id: "landcover-sand",
    type: "fill",
    source: "omt",
    "source-layer": "landcover",
    filter: ["==", ["get", "class"], "sand"],
    paint: { "fill-color": c.sand }
  });
  L.push({
    id: "landcover-ice",
    type: "fill",
    source: "omt",
    "source-layer": "landcover",
    filter: ["==", ["get", "class"], "ice"],
    paint: { "fill-color": c.ice }
  });
  const landuse = [
    ["residential", c.residential, ["residential", "suburb", "neighbourhood"]],
    ["industrial", c.industrial, ["industrial", "commercial", "retail"]],
    ["cemetery", c.cemetery, ["cemetery"]],
    ["hospital", c.hospital, ["hospital"]],
    ["school", c.school, ["school", "university", "college", "kindergarten"]]
  ];
  for (const [id, color, classes] of landuse) {
    L.push({
      id: `landuse-${id}`,
      type: "fill",
      source: "omt",
      "source-layer": "landuse",
      filter: ["in", ["get", "class"], ["literal", classes]],
      paint: { "fill-color": color }
    });
  }
  L.push({
    id: "park",
    type: "fill",
    source: "omt",
    "source-layer": "park",
    paint: { "fill-color": c.park, "fill-opacity": 0.7 }
  });

  // ---------- voda ----------
  L.push({
    id: "waterway",
    type: "line",
    source: "omt",
    "source-layer": "waterway",
    paint: {
      "line-color": c.river,
      "line-width": ["interpolate", ["exponential", 1.4], ["zoom"], 8, 0.5, 20, 8]
    }
  });
  L.push({
    id: "water",
    type: "fill",
    source: "omt",
    "source-layer": "water",
    paint: { "fill-color": c.water, "fill-outline-color": c.waterOutline }
  });

  // ---------- letiská ----------
  L.push({
    id: "aeroway",
    type: "line",
    source: "omt",
    "source-layer": "aeroway",
    filter: ["in", ["get", "class"], ["literal", ["runway", "taxiway"]]],
    paint: {
      "line-color": c.aeroway,
      "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1, 16, 20]
    }
  });

  // ---------- budovy ----------
  L.push({
    id: "building",
    type: "fill",
    source: "omt",
    "source-layer": "building",
    minzoom: 13,
    paint: {
      "fill-color": c.building,
      "fill-outline-color": c.buildingOutline,
      "fill-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0.5, 15, 1]
    }
  });

  // ---------- cesty (podklad – casing, potom výplň) ----------
  const roadWidth = (base, top) =>
    ["interpolate", ["exponential", 1.5], ["zoom"], 5, base, 18, top];

  const notTunnel = ["!=", ["get", "brunnel"], "tunnel"];

  L.push({
    id: "road-path",
    type: "line",
    source: "omt",
    "source-layer": "transportation",
    minzoom: 13,
    filter: ["all", ["in", ["get", "class"], ["literal", ["path", "track"]]], notTunnel],
    paint: {
      "line-color": c.path,
      "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.7, 18, 2.2],
      "line-dasharray": [2, 2]
    }
  });

  const casings = [
    ["motorway-casing", ["motorway"], c.motorwayCasing, roadWidth(1.2, 22), 0],
    ["major-casing", ["trunk", "primary"], c.roadCasing, roadWidth(1.0, 18), 7],
    ["mid-casing", ["secondary", "tertiary"], c.roadCasing, roadWidth(0.8, 14), 9],
    ["minor-casing", ["minor", "service"], c.roadCasing, roadWidth(0.6, 10), 12]
  ];
  for (const [id, classes, color, width, minzoom] of casings) {
    L.push({
      id: `road-${id}`,
      type: "line",
      source: "omt",
      "source-layer": "transportation",
      minzoom,
      filter: ["all", ["in", ["get", "class"], ["literal", classes]], notTunnel],
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": color, "line-width": width }
    });
  }
  const roads = [
    ["motorway", ["motorway"], c.motorway, roadWidth(0.8, 18), 0],
    ["trunk", ["trunk"], c.trunk, roadWidth(0.7, 15), 6],
    ["primary", ["primary"], c.primary, roadWidth(0.7, 15), 7],
    ["secondary", ["secondary", "tertiary"], c.secondary, roadWidth(0.5, 12), 9],
    ["minor", ["minor", "service"], c.minor, roadWidth(0.4, 8), 12]
  ];
  for (const [id, classes, color, width, minzoom] of roads) {
    L.push({
      id: `road-${id}`,
      type: "line",
      source: "omt",
      "source-layer": "transportation",
      minzoom,
      filter: ["all", ["in", ["get", "class"], ["literal", classes]], notTunnel],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": color, "line-width": width }
    });
  }
  L.push({
    id: "rail",
    type: "line",
    source: "omt",
    "source-layer": "transportation",
    minzoom: 10,
    filter: ["==", ["get", "class"], "rail"],
    paint: {
      "line-color": c.rail,
      "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.8, 18, 2.5],
      "line-dasharray": [4, 3]
    }
  });

  // ---------- hranice ----------
  L.push({
    id: "boundary-region",
    type: "line",
    source: "omt",
    "source-layer": "boundary",
    filter: ["==", ["get", "admin_level"], 4],
    paint: {
      "line-color": c.boundary,
      "line-width": 1,
      "line-dasharray": [3, 2],
      "line-opacity": 0.6
    }
  });
  L.push({
    id: "boundary-country",
    type: "line",
    source: "omt",
    "source-layer": "boundary",
    filter: ["==", ["get", "admin_level"], 2],
    paint: {
      "line-color": c.boundary,
      "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1, 12, 3]
    }
  });

  // ---------- popisky ----------
  const nameExpr = ["coalesce", ["get", "name:sk"], ["get", "name"]];

  L.push({
    id: "water-name",
    type: "symbol",
    source: "omt",
    "source-layer": "water_name",
    layout: {
      "text-field": nameExpr,
      "text-font": ["Noto Sans Italic"],
      "text-size": 12
    },
    paint: {
      "text-color": c.waterText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1
    }
  });
  L.push({
    id: "road-name",
    type: "symbol",
    source: "omt",
    "source-layer": "transportation_name",
    minzoom: 13,
    layout: {
      "symbol-placement": "line",
      "text-field": nameExpr,
      "text-font": ["Noto Sans Regular"],
      "text-size": 11
    },
    paint: {
      "text-color": c.roadText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1.2
    }
  });

  // ---------- POI s ikonkami ----------
  L.push({
    id: "poi",
    type: "symbol",
    source: "omt",
    "source-layer": "poi",
    minzoom: 14,
    filter: ["<=", ["get", "rank"], 25],
    layout: {
      "icon-image": [
        "case",
        ["in", ["get", "class"], ["literal", POI_ICONS]],
        ["concat", ["get", "class"], "_11"],
        "circle_11"
      ],
      "icon-size": 1,
      "text-field": nameExpr,
      "text-font": ["Noto Sans Regular"],
      "text-size": 10,
      "text-offset": [0, 1.1],
      "text-anchor": "top",
      "text-optional": true
    },
    paint: {
      "text-color": c.poiText,
      "text-halo-color": c.poiHalo,
      "text-halo-width": 1
    }
  });

  // ---------- sídla ----------
  L.push({
    id: "place-village",
    type: "symbol",
    source: "omt",
    "source-layer": "place",
    minzoom: 10,
    filter: ["in", ["get", "class"], ["literal", ["village", "hamlet", "suburb"]]],
    layout: {
      "text-field": nameExpr,
      "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 10, 10, 15, 14]
    },
    paint: {
      "text-color": c.placeText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1.5
    }
  });
  L.push({
    id: "place-town",
    type: "symbol",
    source: "omt",
    "source-layer": "place",
    filter: ["==", ["get", "class"], "town"],
    layout: {
      "text-field": nameExpr,
      "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 7, 11, 13, 18]
    },
    paint: {
      "text-color": c.placeText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1.5
    }
  });
  L.push({
    id: "place-city",
    type: "symbol",
    source: "omt",
    "source-layer": "place",
    filter: ["==", ["get", "class"], "city"],
    layout: {
      "text-field": nameExpr,
      "text-font": ["Noto Sans Bold"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 5, 12, 12, 22]
    },
    paint: {
      "text-color": c.placeText,
      "text-halo-color": c.placeHalo,
      "text-halo-width": 1.8
    }
  });

  return style;
}
