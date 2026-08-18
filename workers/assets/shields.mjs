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
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { decodePng, encodePng, packShelves } from "../lib/png.mjs";
import { SHIELD_SHAPES, SHIELD_SHAPE_IDS, renderShield } from "../../poc/web/shields.js";

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

/** Doplní štítky do jednej varianty spritu (1× alebo @2×). */
function addTo(suffix, pixelRatio) {
  const jsonPath = `${spriteBase}${suffix}.json`;
  const pngPath = `${spriteBase}${suffix}.png`;
  if (!existsSync(jsonPath) || !existsSync(pngPath)) return false;

  const index = JSON.parse(readFileSync(jsonPath, "utf8"));
  const atlas = decodePng(readFileSync(pngPath));

  // Existujúce obrázky sa z atlasu vyberú, aby sa dal preskladať aj s novými.
  // Štítky, ktoré tam už sú (opakovaný beh nad spritom z cache), sa zahodia
  // a nakreslia znova – inak by v atlase pribúdali kópie.
  const boxes = [];
  for (const [name, e] of Object.entries(index)) {
    if (SHIELD_SHAPE_IDS.includes(name)) continue;
    const data = Buffer.alloc(e.width * e.height * 4);
    for (let y = 0; y < e.height; y++) {
      for (let x = 0; x < e.width; x++) {
        const s = ((e.y + y) * atlas.width + e.x + x) * 4;
        atlas.data.copy(data, (y * e.width + x) * 4, s, s + 4);
      }
    }
    boxes.push({ name, width: e.width, height: e.height, data, entry: e });
  }

  for (const shape of SHIELD_SHAPES) {
    const img = renderShield(shape, pixelRatio);
    boxes.push({
      name: shape.id,
      width: img.width,
      height: img.height,
      data: Buffer.from(img.data.buffer, img.data.byteOffset, img.data.length),
      // `sdf` aj rozťahovacie pásma idú do indexu – bez nich by MapLibre
      // štítok ani nezafarbil, ani nenatiahol (a nepovedal by nič).
      entry: {
        pixelRatio,
        sdf: true,
        stretchX: img.stretchX,
        stretchY: img.stretchY,
        content: img.content
      }
    });
  }

  const packed = packShelves(boxes, suffix ? 1024 : 512);
  const out = Buffer.alloc(packed.width * packed.height * 4);
  const outIndex = {};
  for (const box of boxes) {
    for (let y = 0; y < box.height; y++) {
      box.data.copy(
        out,
        ((box.y + y) * packed.width + box.x) * 4,
        y * box.width * 4,
        (y + 1) * box.width * 4
      );
    }
    outIndex[box.name] = {
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      pixelRatio: box.entry.pixelRatio || 1,
      ...(box.entry.sdf ? { sdf: true } : {}),
      // Rozťahovacie pásma sa musia preniesť aj pri PRESKLADANÍ cudzieho
      // obrázka: keby ich preskladanie stratilo, štítok by sa naťahoval celý
      // aj s rohmi a nikto by nespadol.
      ...(box.entry.stretchX ? { stretchX: box.entry.stretchX } : {}),
      ...(box.entry.stretchY ? { stretchY: box.entry.stretchY } : {}),
      ...(box.entry.content ? { content: box.entry.content } : {})
    };
  }

  writeFileSync(pngPath, encodePng({ ...packed, data: out }));
  writeFileSync(jsonPath, JSON.stringify(outIndex));
  console.log(
    `✓ ${jsonPath}: ${boxes.length} obrázkov (z toho ${SHIELD_SHAPES.length} štítkov ciest), ` +
      `atlas ${packed.width}×${packed.height}`
  );
  return true;
}

if (!addTo("", 1)) {
  console.error(`::error::Sprite ${spriteBase}.json/.png neexistuje`);
  process.exit(1);
}
addTo("@2x", 2);
