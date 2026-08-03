/**
 * Developer mode – ladenie mapy priamo v prehliadači.
 *
 * Čo vie:
 *   - vypísať **všetky** vrstvy štýlu po skupinách, s druhom (plocha / línia /
 *     bod / popisok / 3D / reliéf) a filtrom, zapnúť/vypnúť ich a nastaviť im
 *     rozsah zoomu – teda presne definovať, čo sa kedy zobrazuje,
 *   - zmeniť farbu ktoréhokoľvek prvku: farby vrstvy zvlášť aj celej palety
 *     naraz, vrátane hromadnej editácie výberu a kopírovania hodnôt,
 *   - skryť konkrétne triedy POI (ikonky bodov),
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
  emptyOverrides,
  normalizeOverrides,
  hasOverrides,
  mergedPalette
} from "./themes.js";

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

function storeOverrides(overrides) {
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

/**
 * @param {object} opts
 * @param {HTMLElement} opts.root      prázdny kontajner pre panel
 * @param {() => object} opts.getStyle aktuálny (už upravený) MapLibre štýl
 * @param {() => string} opts.getTheme kľúč aktuálnej témy
 * @param {() => object} opts.getMap   inštancia mapy (na zisťovanie POI tried)
 * @param {(overrides: object) => void} opts.onChange  prekresli mapu
 */
export function initDevMode({ root, getStyle, getTheme, getMap, onChange }) {
  let overrides = loadOverrides();
  let tab = "layers";
  let search = "";
  let kindFilter = new Set();
  const selectedLayers = new Set();
  const selectedPaletteKeys = new Set();
  const collapsed = new Set();
  let poiClasses = [];
  let applyTimer = null;

  // ---------- základná kostra ----------
  const body = el("div", { class: "dev-body" });
  const status = el("div", { class: "dev-status" });
  const tabsBar = el("div", { class: "dev-tabs" });

  const TABS = [
    ["layers", "Vrstvy"],
    ["palette", "Paleta"],
    ["poi", "POI"],
    ["file", "Súbor"]
  ];

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
    storeOverrides(overrides);
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
      ? `Zmeny: ${nPalette} farieb palety · ${nLayers} vrstiev · ${nPoi} skrytých POI · uložené v prehliadači`
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
    const row = el("div", { class: `dev-colorrow${changed ? " changed" : ""}` }, [
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
    return row;
  }

  function zoomControl(layer) {
    const o = layerOverride(layer.id) || {};
    const mk = (prop, label) => {
      const input = el("input", {
        type: "number",
        class: "dev-num",
        min: "0",
        max: "24",
        step: "0.5",
        value: layer[prop] ?? "",
        placeholder: prop === "minzoom" ? "0" : "24"
      });
      input.addEventListener("change", () => {
        const v = input.value === "" ? undefined : Number(input.value);
        setLayerOverride(layer.id, { [prop]: v });
        apply({ immediate: true });
      });
      return el("label", { class: "dev-zoom" }, [
        el("span", { text: label }),
        input
      ]);
    };
    return el("div", { class: `dev-zooms${o.minzoom != null || o.maxzoom != null ? " changed" : ""}` }, [
      mk("minzoom", "od z"),
      mk("maxzoom", "do z")
    ]);
  }

  // ---------- tab: vrstvy ----------
  function visibleLayers() {
    const style = getStyle();
    const q = search.trim().toLowerCase();
    return style.layers.filter((l) => {
      const meta = l.metadata || {};
      const kind = meta["frico:kind"] || "line";
      if (kindFilter.size && !kindFilter.has(kind)) return false;
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
      // vyhľadávanie nesmie stratiť kurzor
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
      const open = !collapsed.has(gid);

      const head = el("div", { class: "dev-group" }, [
        el("button", {
          type: "button",
          class: "dev-groupname",
          text: `${open ? "▾" : "▸"} ${GROUP_LABELS[gid] || gid} (${list.length})`,
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
      ]);

      groups.push(head);
      if (open) for (const layer of list) groups.push(layerRow(layer));
    }

    return el("div", {}, [
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

    const check = el("input", { type: "checkbox", class: "dev-check" });
    check.checked = selectedLayers.has(layer.id);
    check.addEventListener("change", () => {
      if (check.checked) selectedLayers.add(layer.id);
      else selectedLayers.delete(layer.id);
      const label = body.querySelector(".dev-bulklabel");
      if (label) label.textContent = `Vybraných: ${selectedLayers.size}`;
    });

    const head = el("div", { class: `dev-row${o ? " changed" : ""}${hidden ? " off" : ""}` }, [
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
      el("span", { class: "dev-name", title: layer.id }, [
        el("span", { text: meta["frico:label"] || layer.id }),
        el("small", { text: layer.id })
      ])
    ]);

    const paletteMap = meta["frico:palette"] || {};
    const details = el("div", { class: "dev-details" }, [
      zoomControl(layer),
      ...colorProps(layer).map(([prop, value]) => {
        const paletteKey = paletteMap[prop];
        const overridden = !!(o && o.paint && o.paint[prop]);
        return el("div", { class: "dev-prop" }, [
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
        ]);
      })
    ]);

    return el("div", { class: "dev-item" }, [head, details]);
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

  // ---------- tab: POI ----------
  function scanPoiClasses() {
    const map = getMap();
    if (!map) return [];
    const counts = new Map();
    for (const layerId of ["poi-major", "poi-all"]) {
      if (!map.getLayer(layerId)) continue;
      let features = [];
      try {
        features = map.querySourceFeatures("omt", { sourceLayer: "poi" });
      } catch {
        features = [];
      }
      for (const f of features) {
        const key = f.properties?.subclass || f.properties?.class;
        if (key) counts.set(key, (counts.get(key) || 0) + 1);
      }
      break;
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
