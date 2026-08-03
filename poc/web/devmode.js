/**
 * Developer mode – ladenie mapy priamo v prehliadači.
 *
 * Čo vie:
 *   - vypísať **všetky** vrstvy štýlu po skupinách, s druhom (plocha / línia /
 *     bod / popisok / 3D / reliéf) a filtrom, zapnúť/vypnúť ich a nastaviť im
 *     rozsah zoomu – teda presne definovať, čo sa kedy zobrazuje,
 *   - prezerať mapu po zoomoch: nastavíš zoom a zoznam ukáže, ktoré vrstvy
 *     sú na ňom naozaj povolené a ktoré sa orežú,
 *   - zmeniť farbu ktoréhokoľvek prvku: farby vrstvy zvlášť aj celej palety
 *     naraz, vrátane hromadnej editácie výberu a kopírovania hodnôt,
 *   - dať ploche alebo čiare opakujúci sa **vzor** a ľubovoľný **okraj**,
 *     čiare aj prerušovanie,
 *   - skryť konkrétne triedy POI a prepnúť celú sadu ikoniek,
 *   - všetko priebežne ukladá do prehliadača (localStorage), vie to
 *     exportovať do `style-overrides.json` a znovu načítať.
 *
 * Ten istý JSON potom prevezme workflow „Uložiť úpravy štýlu do zdrojáku",
 * uloží ho do repozitára a pipeline ho zapečie do mapy pre web aj iOS.
 */
import {
  THEMES,
  PALETTE_GROUPS,
  PALETTE_LABELS,
  LAYER_GROUPS,
  LAYER_KINDS,
  MAX_DISPLAY_Z,
  emptyOverrides,
  normalizeOverrides,
  hasOverrides,
  mergedPalette,
  selectedIconSource
} from "./themes.js";
import { PATTERNS, DASH_PRESETS } from "./patterns.js";

const STORAGE_KEY = "fricomaps.overrides";
const KIND_LABELS = Object.fromEntries(LAYER_KINDS.map((k) => [k.id, k.label]));
const GROUP_LABELS = Object.fromEntries(LAYER_GROUPS.map((g) => [g.id, g.label]));

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
/** `input[type=color]` vie iba 6-miestny hex – kratšie zápisy dopočítame. */
const toHex6 = (v) => {
  const s = String(v || "").trim();
  if (isHex6(s)) return s.toLowerCase();
  const m = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(s);
  if (m) return `#${m[1]}${m[1]}${m[2]}${m[2]}${m[3]}${m[3]}`.toLowerCase();
  const m8 = /^#([0-9a-f]{6})[0-9a-f]{2}$/i.exec(s);
  if (m8) return `#${m8[1]}`.toLowerCase();
  return "#000000";
};

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
  if (mn <= 0 && layer.maxzoom == null) return "vždy";
  if (layer.maxzoom == null) return `z${mn}+`;
  return `z${mn}–${mx}`;
};

/** Kreslí sa vrstva na danom zoome? */
const activeAt = (layer, z) => {
  if ((layer.layout || {}).visibility === "none") return false;
  return z >= (layer.minzoom ?? 0) && z < (layer.maxzoom ?? 25);
};

/**
 * @param {object} opts
 * @param {HTMLElement} opts.root      prázdny kontajner pre panel
 * @param {() => object} opts.getStyle aktuálny (už upravený) MapLibre štýl
 * @param {() => string} opts.getTheme kľúč aktuálnej témy
 * @param {() => object} opts.getMap   inštancia mapy
 * @param {() => object[]} [opts.getIconSets] nasadené sady ikoniek
 * @param {(overrides: object) => void} opts.onChange  prekresli mapu
 */
export function initDevMode({ root, getStyle, getTheme, getMap, getIconSets, onChange }) {
  let overrides = loadOverrides();
  let tab = "layers";
  let search = "";
  let kindFilter = new Set();
  let onlyActive = false;
  let zoomView = getMap()?.getZoom?.() ?? 10;
  const selectedLayers = new Set();
  const selectedPaletteKeys = new Set();
  const collapsed = new Set();
  const expanded = new Set();
  let poiClasses = [];
  let applyTimer = null;
  let zoomTimer = null;

  // ---------- základná kostra ----------
  const body = el("div", { class: "dev-body" });
  const status = el("div", { class: "dev-status" });
  const tabsBar = el("div", { class: "dev-tabs" });

  const TABS = [
    ["layers", "Vrstvy"],
    ["palette", "Paleta"],
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
    status.textContent = hasOverrides(overrides)
      ? `Zmeny: ${nPalette} farieb palety · ${nLayers} vrstiev · ${nPoi} skrytých POI · ` +
        `ikony ${selectedIconSource(overrides)} · uložené v prehliadači`
      : "Žiadne zmeny – mapa beží na pôvodnom štýle.";
  }

  // ---------- pomocníci nad overrides ----------
  const layerOverride = (id) => overrides.layers[id];

  function setLayerOverride(id, patch) {
    const cur = { ...(overrides.layers[id] || {}) };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete cur[k];
      else cur[k] = v;
    }
    if (cur.paint && !Object.keys(cur.paint).length) delete cur.paint;
    if (Object.keys(cur).length) overrides.layers[id] = cur;
    else delete overrides.layers[id];
  }

  function setLayerPaint(id, prop, value) {
    const cur = { ...((overrides.layers[id] || {}).paint || {}) };
    if (value === undefined) delete cur[prop];
    else cur[prop] = value;
    setLayerOverride(id, { paint: Object.keys(cur).length ? cur : undefined });
  }

  /** Zmení jednu vlastnosť vzoru / okraja bez toho, aby zmazala ostatné. */
  function patchSub(id, key, patch) {
    const cur = { ...((overrides.layers[id] || {})[key] || {}) };
    setLayerOverride(id, { [key]: { ...cur, ...patch } });
  }

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
    const picker = el("input", { type: "color", class: "dev-color", value: hex });
    const text = el("input", { type: "text", class: "dev-hex", value: hex, spellcheck: "false" });
    picker.addEventListener("input", () => {
      text.value = picker.value;
      onInput(picker.value);
    });
    text.addEventListener("change", () => {
      const v = toHex6(text.value);
      text.value = v;
      picker.value = v;
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

  function numberField({ label, value, min, max, step, onChange, placeholder }) {
    const input = el("input", {
      type: "number",
      class: "dev-num",
      min,
      max,
      step,
      value: value ?? "",
      placeholder: placeholder || ""
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
    return (layer.layout || {}).visibility === "none";
  }

  function setVisible(ids, visible) {
    for (const id of ids) setLayerOverride(id, { visible: visible ? undefined : false });
  }

  /** Posuvník zoomu + prehľad, koľko vrstiev je na ňom povolených. */
  function zoomBar() {
    const all = listedLayers();
    const active = all.filter((l) => activeAt(l, zoomView)).length;

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

    const goTo = (z) => {
      zoomView = Math.min(MAX_DISPLAY_Z, Math.max(0, Number(z)));
      getMap()?.jumpTo({ zoom: zoomView });
      renderBody();
    };
    slider.addEventListener("input", () => {
      number.value = slider.value;
    });
    slider.addEventListener("change", () => goTo(slider.value));
    number.addEventListener("change", () => goTo(number.value));

    return el("div", { class: "dev-zoombar" }, [
      el("div", { class: "dev-zoomrow" }, [
        el("span", { class: "dev-zoomlabel", text: `Zoom ${zoomView.toFixed(1)}` }),
        slider,
        number
      ]),
      el("div", { class: "dev-zoominfo" }, [
        el("span", {
          text: `Na tomto zoome kreslí mapa ${active} zo ${all.length} vrstiev.`
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
      el("button", {
        type: "button",
        class: "dev-btn danger",
        text: "Reset výberu",
        onclick: () => {
          for (const id of selectedLayers) delete overrides.layers[id];
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

    const cls = `dev-row${o ? " changed" : ""}${hidden ? " off" : ""}${
      inactive && !hidden ? " inactive" : ""
    }`;
    const head = el("div", { class: cls }, [
      check,
      el("button", {
        type: "button",
        class: "dev-mini",
        title: hidden ? "Zobraziť vrstvu" : "Skryť vrstvu",
        text: hidden ? "🚫" : "👁",
        onclick: () => {
          setLayerOverride(layer.id, { visible: hidden ? undefined : false });
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
      el("span", {
        class: `dev-zrange${inactive ? " off" : ""}`,
        text: zoomRangeText(layer),
        title: inactive
          ? `Na zoome ${zoomView.toFixed(1)} sa nekreslí`
          : `Na zoome ${zoomView.toFixed(1)} sa kreslí`
      })
    ]);

    return el("div", { class: "dev-item" }, [head, open ? layerDetails(layer) : null]);
  }

  function layerDetails(layer) {
    const o = layerOverride(layer.id) || {};
    const paletteMap = (layer.metadata || {})["frico:palette"] || {};
    const parts = [];

    // ---- rozsah zoomu ----
    const zoomField = (prop, label) =>
      numberField({
        label,
        value: layer[prop],
        min: 0,
        max: 24,
        step: 0.5,
        placeholder: prop === "minzoom" ? "0" : "24",
        onChange: (v) => {
          setLayerOverride(layer.id, { [prop]: v });
          apply({ immediate: true });
        }
      });
    parts.push(
      el("div", { class: `dev-fields${o.minzoom != null || o.maxzoom != null ? " changed" : ""}` }, [
        zoomField("minzoom", "od z"),
        zoomField("maxzoom", "do z")
      ])
    );

    // ---- farby ----
    for (const [prop, value] of colorProps(layer)) {
      const paletteKey = paletteMap[prop];
      const overridden = !!(o.paint && o.paint[prop]);
      parts.push(
        el("div", { class: "dev-prop" }, [
          el("span", { class: "dev-propname", text: prop.replace(/-color$/, "") }),
          colorControl({
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
    }

    if (!canDecorate(layer)) return el("div", { class: "dev-details" }, parts);

    // ---- prerušovanie čiary ----
    if (layer.type === "line") {
      parts.push(
        el("div", { class: "dev-sub" }, [
          selectField({
            label: "Čiara",
            value: o.dash || "solid",
            options: DASH_PRESETS.map((d) => [d.id, d.label]),
            onChange: (v) => {
              setLayerOverride(layer.id, { dash: v === "solid" ? undefined : v });
              apply({ immediate: true });
            }
          })
        ])
      );
    }

    // ---- opakujúci sa vzor ----
    const pat = o.pattern;
    const patternRow = [
      selectField({
        label: "Vzor",
        value: pat ? pat.id : "",
        options: [["", "žiadny"], ...PATTERNS.map((p) => [p.id, p.label])],
        onChange: (v) => {
          setLayerOverride(layer.id, {
            pattern: v
              ? { ...(pat || { size: 16, weight: 1, opacity: 1, color: darken(primaryColor(layer)) }), id: v }
              : undefined
          });
          apply({ immediate: true });
        }
      })
    ];
    if (pat) {
      patternRow.push(
        colorControl({
          value: pat.color,
          changed: true,
          onInput: (v) => {
            patchSub(layer.id, "pattern", { color: v });
            apply({ rerender: false });
          }
        }),
        numberField({
          label: "veľkosť",
          value: pat.size,
          min: 4,
          max: 64,
          step: 1,
          onChange: (v) => {
            patchSub(layer.id, "pattern", { size: v ?? 16 });
            apply({ immediate: true });
          }
        }),
        numberField({
          label: "hrúbka",
          value: pat.weight,
          min: 0.5,
          max: 8,
          step: 0.5,
          onChange: (v) => {
            patchSub(layer.id, "pattern", { weight: v ?? 1 });
            apply({ immediate: true });
          }
        }),
        numberField({
          label: "krytie",
          value: pat.opacity,
          min: 0,
          max: 1,
          step: 0.1,
          onChange: (v) => {
            patchSub(layer.id, "pattern", { opacity: v ?? 1 });
            apply({ immediate: true });
          }
        })
      );
    }
    parts.push(el("div", { class: `dev-sub${pat ? " changed" : ""}` }, patternRow));

    // ---- okraj ----
    const out = o.outline;
    const isArea = layer.type !== "line";
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
        selectField({
          label: "vzor",
          value: out.dash || "solid",
          options: DASH_PRESETS.map((d) => [d.id, d.label]),
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
              if (PALETTE_LABELS[k] && isHex6(toHex6(v))) setPaletteColor(k, toHex6(v));
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

  function renderPoi() {
    const hidden = new Set(overrides.poi.hidden);
    const known = new Map(poiClasses);
    for (const h of hidden) if (!known.has(h)) known.set(h, 0);
    const list = [...known.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

    const rows = list.map(([cls, count]) => {
      const check = el("input", { type: "checkbox", class: "dev-check" });
      check.checked = !hidden.has(cls);
      check.addEventListener("change", () => {
        const next = new Set(overrides.poi.hidden);
        if (check.checked) next.delete(cls);
        else next.add(cls);
        overrides.poi.hidden = [...next].sort();
        apply({ rerender: false });
      });
      return el("label", { class: `dev-prow${hidden.has(cls) ? " off" : ""}` }, [
        check,
        el("span", { class: "dev-name" }, [
          el("span", { text: cls }),
          el("small", { text: count ? `${count} v zobrazenom výreze` : "skryté" })
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
          text: "Zobraziť všetky",
          onclick: () => {
            overrides.poi.hidden = [];
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
          "<b>Uložiť úpravy štýlu do zdrojáku</b> (Actions → Run workflow → " +
          "vlož obsah súboru). Pipeline ich zapečie do mapy pre web aj iOS."
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
        : tab === "palette"
        ? renderPalette()
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
    setOverrides(next) {
      overrides = normalizeOverrides(next).overrides;
      apply({ immediate: true });
    }
  };
}
