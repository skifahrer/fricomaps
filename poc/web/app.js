import {
  THEMES,
  buildStyle,
  normalizeOverrides,
  hasOverrides,
  emptyOverrides,
  CLICKABLE_LAYERS,
  MAX_DISPLAY_Z,
  MAX_TILE_Z,
  DEFAULT_DEM_TILES
} from "./themes.js";
import { initDevMode, loadOverrides } from "./devmode.js";

// Základná URL stránky (funguje na GitHub Pages aj lokálne).
const baseUrl = new URL(".", location.href).href.replace(/\/$/, "");

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const $ = (id) => document.getElementById(id);
const themeSelect = $("theme");
const regionSelect = $("region");
const contoursCheck = $("contours");
const terrainCheck = $("terrain");
const devCheck = $("devmode");
const metaEl = $("meta");
const zoomEl = $("zoom");
const warnEl = $("warn");
const panelEl = $("panel");
const toggleEl = $("toggle");
const devEl = $("dev");

for (const [key, t] of Object.entries(THEMES)) {
  themeSelect.add(new Option(t.label, key));
}

// ---------- zbalené ovládanie ----------
function setPanel(open) {
  panelEl.hidden = !open;
  toggleEl.setAttribute("aria-expanded", String(open));
  toggleEl.textContent = open ? "✕" : "⚙";
  try {
    localStorage.setItem("fricomaps.panel", open ? "1" : "0");
  } catch {
    /* súkromný režim – stav si jednoducho nezapamätáme */
  }
}
toggleEl.addEventListener("click", () => setPanel(panelEl.hidden));
setPanel(localStorage.getItem?.("fricomaps.panel") === "1");

function showError(detail) {
  $("error").style.display = "block";
  $("error-detail").textContent = detail || "";
}

/**
 * Chyby pri načítaní dlaždíc, spritu alebo glyfov sa inak prejavia len ako
 * prázdna (biela) mapa – preto ich zbierame a zobrazíme v paneli.
 */
const seenWarnings = new Set();
function warn(message) {
  if (seenWarnings.has(message)) return;
  seenWarnings.add(message);
  warnEl.style.display = "block";
  toggleEl.classList.add("has-warning");
  const li = document.createElement("li");
  li.textContent = message;
  warnEl.querySelector("ul").appendChild(li);
  console.warn("[FricoMaps]", message);
}

async function loadJson(url, { optional = false } = {}) {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    if (!optional) throw new Error(`${url}: ${err.message}`);
    warn(`Nepodarilo sa načítať ${url} (${err.message})`);
    return null;
  }
}

let map;
let dev = null;
/** Úpravy štýlu z developer módu (prehliadač > zdroják > žiadne). */
let overrides = emptyOverrides();
/** Posledný vygenerovaný štýl – developer mode z neho číta zoznam vrstiev. */
let currentStyle = null;
/** Sprite je SDF (ikony sa dajú prefarbiť) – zistí sa zo sprite indexu. */
let sdfIcons = false;

function styleFor(manifest, icons) {
  const region = manifest.regions[regionSelect.value];
  const tileZ = region.maxzoom || manifest.maxzoom || MAX_TILE_Z;
  const demTiles = manifest.dem === null ? null : manifest.dem || DEFAULT_DEM_TILES;

  return buildStyle({
    theme: themeSelect.value,
    tilesUrl: `pmtiles://${baseUrl}/${region.pmtiles}`,
    spriteUrl: manifest.sprite || `${baseUrl}/sprites/osm-liberty`,
    glyphsUrl:
      manifest.glyphs || "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf",
    icons,
    fonts: manifest.fonts,
    maxzoom: tileZ,
    contoursUrl:
      region.contours && contoursCheck.checked
        ? `pmtiles://${baseUrl}/${region.contours}`
        : null,
    contoursMaxzoom: region.contours_maxzoom || 14,
    demTiles,
    sdfIcons,
    overrides,
    name: `FricoMaps – ${region.name}`
  });
}

function applyStyle(manifest, icons) {
  const region = manifest.regions[regionSelect.value];
  const themeKey = themeSelect.value;
  const tileZ = region.maxzoom || manifest.maxzoom || MAX_TILE_Z;

  const style = styleFor(manifest, icons);
  currentStyle = style;

  document.body.classList.toggle("dark", themeKey === "tmava");

  metaEl.innerHTML =
    `Región: <b>${region.name}</b><br>` +
    `Dlaždice do z${tileZ}, zobrazenie do z${MAX_DISPLAY_Z} (overzoom)<br>` +
    (region.contours ? `Vrstevnice po ${region.contour_interval || 10} m<br>` : "") +
    (hasOverrides(overrides) ? "Štýl s vlastnými úpravami (developer mode)<br>" : "") +
    `Vygenerované: ${new Date(manifest.built_at).toLocaleString("sk-SK")}<br>` +
    `© OpenStreetMap prispievatelia`;

  if (!map) {
    const [w, s, e, n] = region.bbox;
    map = new maplibregl.Map({
      container: "map",
      style,
      bounds: [[w, s], [e, n]],
      fitBoundsOptions: { padding: 20 },
      maxZoom: MAX_DISPLAY_Z,
      maxPitch: 75,
      attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    map.addControl(
      new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: true
      }),
      "top-right"
    );

    map.on("error", (ev) => {
      const err = ev?.error;
      const url = err?.url || ev?.sourceId || "";
      warn(`${err?.message || "neznáma chyba"}${url ? ` – ${url}` : ""}`);
    });

    const updateZoom = () => {
      const z = map.getZoom();
      zoomEl.textContent =
        `zoom ${z.toFixed(1)}` + (z > tileZ ? ` · overzoom z${tileZ}` : "");
    };
    map.on("zoom", updateZoom);
    map.on("load", updateZoom);
    map.on("style.load", applyTerrain);

    // Klik na POI / vrchol / letisko zobrazí popup s detailom.
    map.on("click", (ev) => {
      const layers = CLICKABLE_LAYERS.filter((id) => map.getLayer(id));
      const [f] = map.queryRenderedFeatures(ev.point, { layers });
      if (!f) return;
      const p = f.properties;
      const title = p["name:sk"] || p.name || "(bez názvu)";
      const detail = [p.subclass, p.class].filter(Boolean).join(" · ");
      const ele = p.ele ? `<br><small>${p.ele} m n. m.</small>` : "";
      new maplibregl.Popup()
        .setLngLat(ev.lngLat)
        .setHTML(`<b>${title}</b><br><small>${detail}</small>${ele}`)
        .addTo(map);
    });
    map.on("mousemove", (ev) => {
      const layers = CLICKABLE_LAYERS.filter((id) => map.getLayer(id));
      const hit = layers.length && map.queryRenderedFeatures(ev.point, { layers }).length;
      map.getCanvas().style.cursor = hit ? "pointer" : "";
    });
  } else {
    // `diff: true` (default) prekreslí len to, čo sa naozaj zmenilo – vďaka
    // tomu je ladenie farieb v developer móde plynulé.
    map.setStyle(style);
  }
}

/** 3D terén používa ten istý raster-dem zdroj ako tieňovanie reliéfu. */
function applyTerrain() {
  if (!map) return;
  const on = terrainCheck.checked && map.getSource("dem");
  map.setTerrain(on ? { source: "dem", exaggeration: 1.3 } : null);
}

// ---------- developer mode ----------
function setDevMode(on, manifest, icons) {
  devEl.hidden = !on;
  devCheck.checked = on;
  try {
    localStorage.setItem("fricomaps.dev", on ? "1" : "0");
  } catch {
    /* súkromný režim */
  }
  if (!on || dev) return;

  dev = initDevMode({
    root: devEl,
    getStyle: () => currentStyle,
    getTheme: () => themeSelect.value,
    getMap: () => map,
    onChange: (next) => {
      overrides = next;
      applyStyle(manifest, icons);
    }
  });
  devEl.addEventListener("dev-close", () => setDevMode(false, manifest, icons));
}

async function main() {
  let manifest;
  try {
    manifest = await loadJson(`${baseUrl}/tiles/manifest.json`);
  } catch (err) {
    showError(String(err));
    return;
  }

  // Zoznam ikon zo spritu – aby štýl odkazoval len na ikony, ktoré existujú.
  // Z indexu sa zároveň zistí, či je sprite SDF (vtedy sa dajú ikony farbiť).
  const spriteUrl = manifest.sprite || `${baseUrl}/sprites/osm-liberty`;
  const spriteJson = await loadJson(`${spriteUrl}.json`, { optional: true });
  const icons = spriteJson ? Object.keys(spriteJson) : [];
  sdfIcons = spriteJson ? Object.values(spriteJson).some((e) => e && e.sdf) : false;

  // Úpravy štýlu: čo je uložené v prehliadači má prednosť, inak sa vezme to,
  // čo je zapečené v zdrojáku (workflow „Uložiť úpravy štýlu do zdrojáku").
  const stored = loadOverrides();
  if (hasOverrides(stored)) {
    overrides = stored;
  } else {
    const committed = await loadJson(`${baseUrl}/style-overrides.json`, { optional: true });
    if (committed) {
      const { overrides: clean, problems } = normalizeOverrides(committed);
      overrides = clean;
      for (const p of problems) warn(`style-overrides.json: ${p}`);
    }
  }

  for (const [key, r] of Object.entries(manifest.regions)) {
    regionSelect.add(new Option(r.name, key));
  }
  regionSelect.value = manifest.default_region;

  const syncControls = () => {
    const region = manifest.regions[regionSelect.value];
    $("row-contours").hidden = !region.contours;
    $("row-terrain").hidden = manifest.dem === null;
  };
  syncControls();

  themeSelect.addEventListener("change", () => {
    applyStyle(manifest, icons);
    dev?.refresh();
  });
  contoursCheck.addEventListener("change", () => {
    applyStyle(manifest, icons);
    dev?.refresh();
  });
  terrainCheck.addEventListener("change", applyTerrain);
  regionSelect.addEventListener("change", () => {
    syncControls();
    applyStyle(manifest, icons);
    const [w, s, e, n] = manifest.regions[regionSelect.value].bbox;
    map.fitBounds([[w, s], [e, n]], { padding: 20 });
  });
  devCheck.addEventListener("change", () =>
    setDevMode(devCheck.checked, manifest, icons)
  );

  applyStyle(manifest, icons);

  // Developer mode sa zapína prepínačom v paneli alebo cez ?dev=1.
  const wantDev =
    new URLSearchParams(location.search).get("dev") === "1" ||
    localStorage.getItem?.("fricomaps.dev") === "1";
  if (wantDev) setDevMode(true, manifest, icons);
}

main();
