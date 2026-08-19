#!/usr/bin/env node
/**
 * Dopečie do spritu ROZŤAHOVATEĽNÉ PODKLADY ŠTÍTKOV s číslom cesty
 * („D1", „R1", „I/18").
 *
 * Beží hneď po `workers/assets/sprite.mjs`, teda nad hotovým SDF spritom –
 * a musí to byť ZVLÁŠŤ, lebo `sprite.mjs` odpovedá na inú otázku („ako z cudzej
 * sady ikoniek spraviť SDF symboly"), kým tu ide o obrázok, ktorý si kreslíme
 * sami a v žiadnej sade ikoniek nie je.
 *
 * PREČO SA PODKLAD NEDÁ NAHRADIŤ SAMOTNÝM HALOM POPISKU. `text-halo-width`
 * obtiahne písmená, nie obdĺžnik – z „D1" cez les vyjde biely obrys písmen,
 * nie značka. Značka musí mať rovnú hranu, aby sa na mape čítala ako značka.
 * (Štýl to aj tak vie: keď štítok v sprite NIE JE, kreslí číslo cesty len
 * s hrubým halom. Vyzerá to horšie, ale nezmizne to.)
 *
 * Tvar, veľkosť aj rozťahovacie pásma sú v `poc/web/shields.js` – jedno
 * miesto pre pipeline aj pre štýl, ktorý sa na tie mená odkazuje.
 *
 * Použitie:
 *   node workers/assets/shields.mjs --sprite=_site/sprites/osm-liberty
 */
import { bakeIntoSprite } from "../lib/sprite-bake.mjs";
import { SHIELD_SHAPES, renderShield } from "../../poc/web/shields.js";
import { THEMES, SHIELD_DEFS } from "../../poc/web/themes.js";

/**
 * Mená štítkov, ktoré tento skript pečie: tvar × trieda cesty × téma.
 *
 * Je ich toľko preto, že štítok je HOTOVÝ FAREBNÝ obrázok, nie SDF – dva
 * rovnako hrubé prstence (biely a za ním farebný) sa cez `icon-color`
 * a jedno `icon-halo` spraviť nedajú. Farba sa tým pečie pri builde, takže
 * každá kombinácia potrebuje vlastný obrázok. Sú to jednotky kB.
 *
 * TVAR JE V MENE, a nie je to výzdoba: developer mode ho vie prepnúť
 * (`overrides.shields`), takže v sprite musia byť naraz VŠETKY tvary –
 * prepnutie v prehliadači sprite neprebuildováva.
 */
function stitky() {
  const out = [];
  for (const shape of SHIELD_SHAPES) {
    for (const [id, , , colorKey, , , , borderKey] of SHIELD_DEFS) {
      for (const [temaKey, tema] of Object.entries(THEMES)) {
        out.push({
          name: `${shape.id}-${id}-${temaKey}`,
          shape,
          colors: { field: tema[colorKey], ring: tema[borderKey] }
        });
      }
    }
  }
  return out;
}

const STITKY = stitky();
const MENA = new Set(STITKY.map((s) => s.name));

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.join("=") || "true"];
  })
);

const spriteBase = args.sprite;
if (!spriteBase) {
  console.error("Použitie: node workers/assets/shields.mjs --sprite=<base>");
  process.exit(2);
}

const ok = bakeIntoSprite({
  spriteBase,
  co: "štítkov ciest",
  mine: (name) => MENA.has(name),
  make: (pixelRatio) =>
    STITKY.map((st) => {
      const img = renderShield(st.shape, st.colors, pixelRatio);
      return {
        name: st.name,
        image: img,
        // BEZ `sdf`: obrázok je už farebný. Rozťahovacie pásma naopak treba –
        // na bežnom rastri fungujú správne a držia štítok obdĺžnikom aj pri
        // dlhom čísle.
        entry: { stretchX: img.stretchX, stretchY: img.stretchY, content: img.content }
      };
    })
});

if (!ok) {
  console.error(`::error::Sprite ${spriteBase}.json/.png neexistuje`);
  process.exit(1);
}
