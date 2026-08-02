import { THEMES, buildStyle, CLICKABLE_LAYERS, MAX_DISPLAY_Z, MAX_TILE_Z } from "./themes.js";

// Základná URL stránky (funguje na GitHub Pages aj lokálne).
const baseUrl = new URL(".", location.href).href.replace(/\/$/, "");

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const themeSelect = document.getElementById("theme");
const regionSelect = document.getElementById("region");
const metaEl = document.getElementById("meta");
const zoomEl = document.getElementById("zoom");
const warnEl = document.getElementById("warn");

for (const [key, t] of Object.entries(THEMES)) {
  themeSelect.add(new Option(t.label, key));
}

function showError(detail) {
  document.getElementById("error").style.display = "block";
  document.getElementById("error-detail").textContent = detail || "";
}

/**
 * Chyby pri načítaní dlaždíc, spritu alebo glyfov sa inak prejavia len ako
 * prázdna (biela) mapa – preto ich zbierame a zobrazíme priamo v paneli.
 */
const seenWarnings = new Set();
function warn(message) {
  if (seenWarnings.has(message)) return;
  seenWarnings.add(message);
  warnEl.style.display = "block";
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

function applyStyle(manifest, icons) {
  const regionKey = regionSelect.value;
  const themeKey = themeSelect.value;
  const region = manifest.regions[regionKey];

  const style = buildStyle({
    theme: themeKey,
    tilesUrl: `pmtiles://${baseUrl}/${region.pmtiles}`,
    spriteUrl: manifest.sprite || `${baseUrl}/sprites/osm-liberty`,
    glyphsUrl:
      manifest.glyphs || "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf",
    icons,
    fonts: manifest.fonts,
    maxzoom: region.maxzoom || manifest.maxzoom || MAX_TILE_Z,
    name: `FricoMaps – ${region.name}`
  });

  document.body.classList.toggle("dark", themeKey === "tmava");

  const tileZ = region.maxzoom || manifest.maxzoom || MAX_TILE_Z;
  metaEl.innerHTML =
    `Región: <b>${region.name}</b><br>` +
    `Dlaždice do z${tileZ}, zobrazenie do z${MAX_DISPLAY_Z} (overzoom)<br>` +
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
      maxPitch: 60,
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
        `zoom ${z.toFixed(1)}` + (z > tileZ ? ` (overzoom z${tileZ})` : "");
    };
    map.on("zoom", updateZoom);
    map.on("load", updateZoom);

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
    map.setStyle(style);
  }
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
  const spriteUrl = manifest.sprite || `${baseUrl}/sprites/osm-liberty`;
  const spriteJson = await loadJson(`${spriteUrl}.json`, { optional: true });
  const icons = spriteJson ? Object.keys(spriteJson) : [];

  for (const [key, r] of Object.entries(manifest.regions)) {
    regionSelect.add(new Option(r.name, key));
  }
  regionSelect.value = manifest.default_region;

  themeSelect.addEventListener("change", () => applyStyle(manifest, icons));
  regionSelect.addEventListener("change", () => {
    applyStyle(manifest, icons);
    const [w, s, e, n] = manifest.regions[regionSelect.value].bbox;
    map.fitBounds([[w, s], [e, n]], { padding: 20 });
  });

  applyStyle(manifest, icons);
}

main();
