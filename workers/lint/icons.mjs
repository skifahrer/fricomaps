#!/usr/bin/env node
/**
 * Kontrola IKON: vlastných obrázkov, vlastných sád a `layout` vlastností.
 * Volá ju `Kontrola · lint workflowov`.
 *
 * Sú to štyri tiché veci – nič z toho nič nezhodí a všetko sa prejaví až
 * v mape (alebo v nej naopak nebude vidieť nič):
 *
 * 1. **Vlastná ikona, ktorá sa nedopečie.** Obrázok leží v úpravách ako PNG
 *    v `data:` adrese; `workers/assets/custom-icons.mjs` ho vkladá do každého
 *    spritu. Keby ho tam nevložil (iná predpona mena, pokazené PNG), MapLibre
 *    neznámy obrázok TICHO preskočí a vrstva ostane bez ikony. Skúša sa to
 *    na naozajstnom sprite: úprava prejde tým skriptom a meno musí byť
 *    v indexe.
 *
 * 2. **Vlastná ikona, ktorú štýl nepustí.** `hasIcon` v `poc/web/themes.js`
 *    ju musí pokladať za dostupnú aj vtedy, keď v sprite ešte nie je (prvé
 *    pridanie v prehliadači) – inak by sa dala vybrať, ale mapa by ju
 *    nenakreslila a vyzeralo by to, že sa vlastné ikony „nedajú".
 *
 * 3. **Vlastná sada, ktorú nikto nesťahuje.** Sadu z úprav musí `icons.sh`
 *    vypísať do zoznamu na stiahnutie a `deploy/site.sh` do manifestu –
 *    inak sa dá pridať a vybrať, ale sprite k nej nikdy nevznikne.
 *
 * 4. **`layout` na inej než symbolovej vrstve.** Neznáma vlastnosť v `layout`
 *    je pre MapLibre TVRDÁ chyba – neodmietne vrstvu, ale CELÝ štýl, takže by
 *    sa mapa nenačítala vôbec. `applyLayerOverrides` ich preto inde než na
 *    `symbol` nenasadzuje a toto to drží.
 *
 * Spustenie (aj lokálne):
 *   node workers/lint/icons.mjs
 */
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { encodePng } from "../lib/png.mjs";
import {
  THEMES,
  buildStyle,
  normalizeOverrides,
  LAYOUT_PROP_IDS,
  CUSTOM_ICON_PREFIX
} from "../../poc/web/themes.js";
import { CUSTOM_SET_PREFIX, allIconSources } from "../../poc/web/icon-sources.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
let bad = 0;
const chyba = (subor, text) => {
  console.log(`::error file=${subor}::${text}`);
  bad += 1;
};

/** Malé PNG ako vlastná ikona. */
const ikonaPng = () => {
  const n = 8;
  const d = Buffer.alloc(n * n * 4, 255);
  return `data:image/png;base64,${encodePng({ width: n, height: n, data: d }).toString("base64")}`;
};

const UKAZKA = {
  version: 2,
  icons: `${CUSTOM_SET_PREFIX}test`,
  iconSets: [
    {
      id: `${CUSTOM_SET_PREFIX}test`,
      label: "Testovacia sada",
      sprite: "https://example.org/sprites/test",
      suffix: "_11"
    }
  ],
  customIcons: [{ name: `${CUSTOM_ICON_PREFIX}test`, png: ikonaPng(), pixelRatio: 2 }]
};
const { overrides, problems } = normalizeOverrides(UKAZKA);
for (const p of problems) {
  chyba("poc/web/themes.js", `ukážkové úpravy neprešli normalizáciou: ${p}`);
}

// ---------- 1. vlastná ikona sa dopečie do spritu ----------
const dir = mkdtempSync(join(tmpdir(), "icons-lint-"));
try {
  const base = join(dir, "sprite");
  writeFileSync(`${base}.png`, encodePng({ width: 4, height: 4, data: Buffer.alloc(64, 255) }));
  writeFileSync(
    `${base}.json`,
    JSON.stringify({ test_11: { x: 0, y: 0, width: 4, height: 4, pixelRatio: 1, sdf: true } })
  );
  const upravy = join(dir, "overrides.json");
  writeFileSync(upravy, JSON.stringify(UKAZKA));
  execFileSync(
    "node",
    ["workers/assets/custom-icons.mjs", `--sprite=${base}`, `--overrides=${upravy}`],
    { stdio: "pipe", cwd: ROOT }
  );
  const index = JSON.parse(readFileSync(`${base}.json`, "utf8"));
  const meno = overrides.customIcons[0].name;
  const e = index[meno];
  if (!e) {
    chyba(
      "workers/assets/custom-icons.mjs",
      `vlastná ikona "${meno}" sa do spritu nedopiekla – vrstva, ktorá si ju pýta, ` +
        `ostane bez obrázka a MapLibre o tom nepovie nič.`
    );
  } else {
    if (e.sdf) {
      chyba("workers/assets/custom-icons.mjs",
        `vlastná ikona "${meno}" je označená ako \`sdf\` – je to hotový farebný obrázok.`);
    }
    if (e.pixelRatio !== 2) {
      chyba("workers/assets/custom-icons.mjs",
        `vlastná ikona "${meno}" má v indexe pixelRatio ${e.pixelRatio}, ale nahrala sa ako @2x ` +
        `– v mape by bola dvojnásobne veľká.`);
    }
  }
  // Pôvodné ikony sa pri tom nesmú stratiť.
  if (!index.test_11) {
    chyba("workers/lib/sprite-bake.mjs",
      "dopečenie vlastných ikon zahodilo ikonu, ktorá v sprite už bola.");
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}

// ---------- 2. štýl vlastnú ikonu pustí, aj keď v sprite ešte nie je ----------
{
  const meno = overrides.customIcons[0].name;
  const style = buildStyle({
    theme: Object.keys(THEMES)[0],
    tilesUrl: "pmtiles://x/t.pmtiles",
    spriteUrl: "https://x/sprite",
    glyphsUrl: "https://x/{fontstack}/{range}.pbf",
    // Sprite zámerne BEZ tej ikony – presne stav po jej pridaní v paneli.
    icons: ["mountain_11"],
    iconSet: "osm-liberty",
    overrides: { ...overrides, layers: { "poi-major": { icon: meno } } }
  });
  const l = style.layers.find((x) => x.id === "poi-major");
  if (!l || (l.layout || {})["icon-image"] !== meno) {
    chyba(
      "poc/web/themes.js",
      `vrstva si po výbere vlastnej ikony "${meno}" nechala ` +
        `"${(l?.layout || {})["icon-image"]}" – práve pridaná ikona sa tým ticho zahodí.`
    );
  }
}

// ---------- 3. vlastnú sadu naozaj niekto stiahne a zapíše do manifestu ----------
{
  const id = overrides.iconSets[0].id;
  if (!allIconSources(overrides).some((s) => s.id === id)) {
    chyba("poc/web/icon-sources.js", `vlastná sada "${id}" nie je v \`allIconSources\`.`);
  }
  for (const [subor, co] of [
    ["workers/assets/icons.sh", "sťahovanie sád"],
    ["workers/deploy/site.sh", "zoznam sád v manifeste"]
  ]) {
    const text = readFileSync(join(ROOT, subor), "utf8");
    if (!text.includes("allIconSources")) {
      chyba(
        subor,
        `${co} berie len sady z repozitára (\`ICON_SOURCES\`). Vlastná sada z úprav ` +
          `sa tým dá pridať aj vybrať, ale sprite k nej nikdy nevznikne – a mapa ` +
          `ostane bez ikon.`
      );
    }
  }
}

// ---------- 4. `layout` sa nasadí len na symbolovú vrstvu ----------
{
  const style = buildStyle({
    theme: Object.keys(THEMES)[0],
    tilesUrl: "pmtiles://x/t.pmtiles",
    spriteUrl: "https://x/sprite",
    glyphsUrl: "https://x/{fontstack}/{range}.pbf",
    icons: ["mountain_11"],
    overrides: normalizeOverrides({
      // `road-path` je čiara – `icon-size` na nej je pre MapLibre neznáma
      // vlastnosť a zhodil by CELÝ štýl.
      layers: { "road-path": { layout: { "icon-size": 2 } } }
    }).overrides
  });
  const l = style.layers.find((x) => x.id === "road-path");
  if (l && (l.layout || {})["icon-size"] !== undefined) {
    chyba(
      "poc/web/themes.js",
      "`layout` úprava sa nasadila na čiarovú vrstvu – MapLibre by taký štýl " +
        "odmietol celý a mapa by sa nenačítala."
    );
  }
}

console.log(
  `ikony: ${bad} chýb (${LAYOUT_PROP_IDS.length} vlastností z layout, ` +
    `${overrides.customIcons.length} vlastných ikon, ${overrides.iconSets.length} vlastných sád)`
);
process.exit(bad ? 1 : 0);
