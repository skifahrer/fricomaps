/**
 * NÁHĽAD VZORU A JEHO VÝBER – a vlastný obrázok ako vzor.
 *
 * Vzor sa dovtedy vyberal z rozbaľovačky s menami („Šrafovanie /", „Šupiny
 * (skaly)") a to je pri vzore ešte horšie než pri ikone: meno nepovie ani ako
 * je hustý, ani ako je hrubý, ani ako vyzerá nad farbou, ktorú tá plocha má.
 * Vybrať ten správny znamenalo prepínať jeden po druhom a pozerať sa do mapy.
 * Preto je z toho MRIEŽKA S NÁHĽADMI, a každý náhľad je kreslený TÝM ISTÝM
 * rasterizérom (`renderPattern`), aký vyrobí obrázok pre mapu aj pre sprite –
 * teda presne to, čo v mape naozaj bude.
 *
 * NÁHĽAD SA KRESLÍ NAD FARBOU PODKLADU. Vzor je vždy druhá vrstva nad plochou
 * (`fill-pattern` sa nedá kresliť do tej istej vrstvy ako výplň), takže sám
 * o sebe nehovorí nič: šrafovanie na bielom pozadí panela vyzerá inak než to
 * isté šrafovanie nad tmavozeleným lesom. Preto sa podklad vypĺňa farbou
 * vrstvy a až na ňu ide vzor – aj s jeho krytím.
 *
 * VLASTNÝ OBRÁZOK je nahratý súbor, ktorý sa dlaždicuje namiesto kresleného
 * vzoru. Je uložený ako VLASTNÁ IKONA úprav (`own:…`, PNG v `data:` adrese),
 * a to je celý dôvod, prečo preň netreba nič ďalšie: obrázok sa nesie spolu
 * s úpravami (funguje offline aj v balíku pre mobil), prehliadač ho dokreslí
 * cez `styleimagemissing` a do každého spritu ho dopečie
 * `workers/assets/custom-icons.mjs`. Druhá pečiaca cesta by bola druhé miesto,
 * ktoré sa raz rozíde s prvým.
 *
 * VEĽKOSŤ DLAŽDICE SA URČUJE PRI NAHRATÍ. Obrázok sa prevzorkuje na zvolenú
 * stranu a uloží sa už taký – čo je v úpravách, je presne to, čo mapa
 * dlaždicuje. Druhá mierka „až pri kreslení" by znamenala, že sprite a
 * prehliadač musia škálovať rovnako, a to je práve ten druh dvoch právd,
 * z ktorých sa raz stane tichý rozdiel medzi webom a mobilom.
 */
import { el } from "./dom.js";
import { PATTERNS, patternSpec, renderPattern } from "./patterns.js";

/** Strana dlaždice vlastného obrázka v pixeloch – čo ponúka výber pri nahratí. */
export const IMAGE_PATTERN_SIZES = [16, 24, 32, 48, 64, 96];

/** Predvolená strana dlaždice vlastného obrázka. */
export const IMAGE_PATTERN_SIZE = 32;

/** Vlastný obrázok z úprav podľa mena (`null`, ak taký nie je). */
const ownImage = (custom, name) => (custom || []).find((c) => c.name === name) || null;

/**
 * Vykreslí vzor na plátno: podklad vo farbe vrstvy a nad ním dlaždicovaný
 * vzor s jeho krytím. `spec` = `null` znamená „bez vzoru" – ostane podklad.
 *
 * Vlastný obrázok sa načítava asynchrónne (je to `data:` adresa), takže sa
 * plátno dokreslí až v `onload`. Preto sa najprv vždy vyplní podklad: prázdne
 * biele miesto by vyzeralo ako pokazený náhľad, nie ako „ešte sa načítava".
 */
export function drawPattern(canvas, spec, background, custom = []) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width: w, height: h } = canvas;
  ctx.clearRect(0, 0, w, h);
  if (background && background !== "none") {
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, w, h);
  }
  if (!spec) return;

  const s = patternSpec(spec);
  const opacity = Number.isFinite(Number(spec.opacity)) ? Number(spec.opacity) : 1;
  const vypln = (zdroj) => {
    const vzor = ctx.createPattern(zdroj, "repeat");
    if (!vzor) return;
    ctx.globalAlpha = Math.min(1, Math.max(0, opacity));
    ctx.fillStyle = vzor;
    ctx.fillRect(0, 0, w, h);
    ctx.globalAlpha = 1;
  };

  if (s.image) {
    const own = ownImage(custom, s.image);
    if (!own) return;
    const img = new Image();
    img.onload = () => {
      // `pixelRatio` je to, čím MapLibre obrázok zmenšuje – náhľad musí robiť
      // to isté, inak by @2x obrázok vyzeral v paneli dvakrát väčší než v mape.
      const pr = own.pixelRatio || 1;
      const off = document.createElement("canvas");
      off.width = Math.max(1, Math.round(img.width / pr));
      off.height = Math.max(1, Math.round(img.height / pr));
      off.getContext("2d").drawImage(img, 0, 0, off.width, off.height);
      vypln(off);
    };
    img.src = own.png;
    return;
  }

  const tile = renderPattern(s, 1);
  const off = document.createElement("canvas");
  off.width = tile.width;
  off.height = tile.height;
  off.getContext("2d").putImageData(new ImageData(tile.data, tile.width, tile.height), 0, 0);
  vypln(off);
}

/** Plátno s náhľadom vzoru nad podkladom. */
export function patternPreview({ spec, background, custom, width = 76, height = 34, title }) {
  const canvas = el("canvas", {
    class: "dev-pat",
    width: String(width),
    height: String(height),
    title: title || ""
  });
  drawPattern(canvas, spec, background, custom);
  return canvas;
}

/**
 * MRIEŽKA VZOROV NA VÝBER.
 *
 * Kreslené vzory sa ukazujú s TERAJŠOU farbou, veľkosťou a hrúbkou (`spec`),
 * nie s nejakými predvolenými: prepnutie vzoru tie tri hodnoty nemení, takže
 * náhľad má ukazovať to, čo z prepnutia naozaj vyjde.
 *
 * @param {object} opts
 * @param {object|null} opts.spec     účinný vzor vrstvy (`null` = žiadny)
 * @param {string} opts.background    farba podkladu (výplň vrstvy)
 * @param {object[]} opts.custom      vlastné ikony z úprav (`{name, png}`)
 * @param {(spec: object|null) => void} opts.onPick
 */
export function patternPicker({ spec, background, custom = [], onPick }) {
  const zaklad = spec && !spec.image ? patternSpec(spec) : {};
  const opacity = spec?.opacity;
  const vybrany = spec ? (spec.image ? `img:${spec.image}` : patternSpec(spec).id) : "";

  const dlazdica = (kluc, popis, nahlad, hodnota) =>
    el("button", {
      type: "button",
      class: `dev-pattile${kluc === vybrany ? " on" : ""}`,
      title: popis,
      onclick: () => onPick(hodnota)
    }, [nahlad, el("small", { text: popis })]);

  const tiles = [
    dlazdica(
      "",
      "žiadny",
      patternPreview({ spec: null, background, custom, width: 64, height: 30 }),
      null
    ),
    ...PATTERNS.map((p) =>
      dlazdica(
        p.id,
        p.label,
        patternPreview({
          spec: { ...zaklad, id: p.id, opacity },
          background,
          custom,
          width: 64,
          height: 30
        }),
        { ...zaklad, id: p.id, opacity }
      )
    ),
    ...(custom || []).map((c) =>
      dlazdica(
        `img:${c.name}`,
        c.name.replace(/^own:/, ""),
        patternPreview({
          spec: { image: c.name, opacity },
          background,
          custom,
          width: 64,
          height: 30
        }),
        { image: c.name, opacity }
      )
    )
  ];

  return el("div", { class: "dev-patgrid" }, tiles);
}
