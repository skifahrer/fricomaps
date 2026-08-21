#!/usr/bin/env node
/**
 * Dopečie do spritu VLASTNÉ IKONY z úprav developer módu.
 *
 * Vlastná ikona je obrázok, ktorý si človek nahral v paneli („toto POI chcem
 * s vlastnou značkou"). Leží PRIAMO v `poc/web/style-overrides.json` ako PNG
 * v `data:` adrese – nie ako odkaz na cudzí server –, takže:
 *
 *   * mapa ju má aj bez internetu a aj v balíku pre mobil,
 *   * a tento skript ju len dekóduje a vloží do atlasu; rasterizovať SVG
 *     v Node netreba, o to sa postaral prehliadač pri nahratí
 *     (`poc/web/dev-icons.js`).
 *
 * Beží po `sprite.mjs`, `shields.mjs` a `marks.mjs` nad hotovým atlasom;
 * preskladanie robí `workers/lib/sprite-bake.mjs` – jedno miesto pre všetkých,
 * čo do spritu pečú.
 *
 * BEZ `sdf`: je to hotový farebný obrázok tak, ako ho človek nahral. Prefarbiť
 * sa teda nedá (`icon-color` na ňom neplatí) a je to zámer – kto chce inú
 * farbu, nahrá iný obrázok.
 *
 * Použitie:
 *   node workers/assets/custom-icons.mjs --sprite=_site/sprites/osm-liberty \
 *        [--overrides=poc/web/style-overrides.json]
 */
import { readFileSync, existsSync } from "node:fs";
import { bakeIntoSprite } from "../lib/sprite-bake.mjs";
import { decodePng } from "../lib/png.mjs";
import { normalizeOverrides, CUSTOM_ICON_PREFIX } from "../../poc/web/themes.js";

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.join("=") || "true"];
  })
);

const spriteBase = args.sprite;
if (!spriteBase) {
  console.error("Použitie: node workers/assets/custom-icons.mjs --sprite=<base>");
  process.exit(2);
}
const overridesPath = args.overrides || "poc/web/style-overrides.json";

let raw = {};
if (existsSync(overridesPath)) {
  try {
    raw = JSON.parse(readFileSync(overridesPath, "utf8"));
  } catch (err) {
    console.error(`::error::${overridesPath} sa nedá prečítať: ${err.message}`);
    process.exit(1);
  }
}
const { overrides, problems } = normalizeOverrides(raw);
for (const p of problems) console.log(`::warning::${p}`);

const IKONY = [];
for (const ikona of overrides.customIcons) {
  const base64 = ikona.png.slice(ikona.png.indexOf(",") + 1);
  let img;
  try {
    img = decodePng(Buffer.from(base64, "base64"));
  } catch (err) {
    // Nedekódovateľná ikona nesmie zhodiť sprite – ale ani ticho zmiznúť:
    // vrstva, ktorá ju používa, ostane bez obrázka a to je vidieť len v mape.
    console.log(`::warning::Vlastná ikona "${ikona.name}" nie je čitateľné PNG (${err.message}) – vynechávam.`);
    continue;
  }
  IKONY.push({ name: ikona.name, image: img, pixelRatio: ikona.pixelRatio || 1 });
}

if (!IKONY.length) {
  console.log("Žiadne vlastné ikony – sprite zostáva bez zmeny.");
  process.exit(0);
}

const ok = bakeIntoSprite({
  spriteBase,
  co: "vlastných ikon",
  mine: (name) => name.startsWith(CUSTOM_ICON_PREFIX),
  make: () =>
    IKONY.map(({ name, image, pixelRatio }) => ({
      name,
      image,
      // `pixelRatio` obrázka je jeho vlastný – prehliadač ho ukladá v @2x,
      // takže by sa pri ratio 1 kreslil dvojnásobne veľký.
      entry: { pixelRatio }
    }))
});

if (!ok) {
  console.error(`::error::Sprite ${spriteBase}.json/.png neexistuje`);
  process.exit(1);
}
console.log(`Vlastné ikony: ${IKONY.map((i) => i.name).join(", ")}`);
