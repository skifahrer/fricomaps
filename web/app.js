import { THEMES, buildStyle } from "./themes.js";

const GLYPHS_URL = "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf";

// Základná URL stránky (funguje na GitHub Pages aj lokálne).
const baseUrl = new URL(".", location.href).href.replace(/\/$/, "");
const spriteUrl = `${baseUrl}/sprites/osm-liberty`;

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const themeSelect = document.getElementById("theme");
const regionSelect = document.getElementById("region");
const metaEl = document.getElementById("meta");

for (const [key, t] of Object.entries(THEMES)) {
  themeSelect.add(new Option(t.label, key));
}

function showError(detail) {
  document.getElementById("error").style.display = "block";
  document.getElementById("error-detail").textContent = detail || "";
}

async function loadManifest() {
  const res = await fetch(`${baseUrl}/tiles/manifest.json`, { cache: "no-store" });
  if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
  return res.json();
}

let map;

function applyStyle(manifest) {
  const regionKey = regionSelect.value;
  const themeKey = themeSelect.value;
  const region = manifest.regions[regionKey];

  const style = buildStyle({
    theme: themeKey,
    tilesUrl: `pmtiles://${baseUrl}/${region.pmtiles}`,
    spriteUrl,
    glyphsUrl: GLYPHS_URL,
    name: `FricoMaps – ${region.name}`
  });

  document.body.classList.toggle("dark", themeKey === "tmava");
  metaEl.innerHTML =
    `Región: <b>${region.name}</b><br>` +
    `Vygenerované: ${new Date(manifest.built_at).toLocaleString("sk-SK")}<br>` +
    `© OpenStreetMap prispievatelia`;

  if (!map) {
    const [w, s, e, n] = region.bbox;
    map = new maplibregl.Map({
      container: "map",
      style,
      bounds: [[w, s], [e, n]],
      fitBoundsOptions: { padding: 20 },
      maxZoom: 18,
      attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));
    map.addControl(
      new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: true
      }),
      "top-right"
    );

    // Klik na POI zobrazí popup s názvom a triedou objektu.
    map.on("click", "poi", (ev) => {
      const f = ev.features[0];
      const name = f.properties["name:sk"] || f.properties.name || "(bez názvu)";
      new maplibregl.Popup()
        .setLngLat(ev.lngLat)
        .setHTML(`<b>${name}</b><br><small>${f.properties.class || ""}</small>`)
        .addTo(map);
    });
    map.on("mouseenter", "poi", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "poi", () => (map.getCanvas().style.cursor = ""));
  } else {
    map.setStyle(style);
  }
}

async function main() {
  let manifest;
  try {
    manifest = await loadManifest();
  } catch (err) {
    showError(String(err));
    return;
  }

  for (const [key, r] of Object.entries(manifest.regions)) {
    regionSelect.add(new Option(r.name, key));
  }
  regionSelect.value = manifest.default_region;

  themeSelect.addEventListener("change", () => applyStyle(manifest));
  regionSelect.addEventListener("change", () => {
    applyStyle(manifest);
    const [w, s, e, n] = manifest.regions[regionSelect.value].bbox;
    map.fitBounds([[w, s], [e, n]], { padding: 20 });
  });

  applyStyle(manifest);
}

main();
