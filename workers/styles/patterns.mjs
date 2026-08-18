#!/usr/bin/env node
/**
 * Dopečie do spritu obrázky opakujúcich sa vzorov, ktoré používa hotový štýl.
 *
 * Vzory (`fill-pattern`, `line-pattern`) sa v developer móde nastavujú
 * predpisom, ktorý je zároveň názvom obrázka (`pat:hatch:3a5a34:16:12`).
 * V prehliadači si ich mapa dokreslí sama, ale statický `style.json` pre iOS
 * musí mať obrázky priamo v sprite – tento skript prejde vygenerované štýly,
 * pozbiera z nich názvy vzorov a doplní ich do atlasu (aj do varianty @2x).
 *
 * Použitie:
 *   node workers/styles/patterns.mjs \
 *        --sprite=_site/sprites/osm-liberty-sdf --styles=_site/styles
 */
import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { decodePng, encodePng, packShelves } from "../lib/png.mjs";
import { collectPatternNames, parsePatternName, renderPattern } from "../../poc/web/patterns.js";

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, "").split("=");
    return [k, v.join("=") || "true"];
  })
);

const spriteBase = args.sprite;
const stylesDir = args.styles;
if (!spriteBase || !stylesDir) {
  console.error(
    "Použitie: node workers/styles/patterns.mjs --sprite=<base> --styles=<dir>"
  );
  process.exit(2);
}

// ---------- ktoré vzory štýly vôbec používajú ----------
const names = new Set();
if (existsSync(stylesDir)) {
  for (const file of readdirSync(stylesDir).filter((f) => f.endsWith(".json"))) {
    try {
      for (const n of collectPatternNames(JSON.parse(readFileSync(join(stylesDir, file), "utf8")))) {
        names.add(n);
      }
    } catch (err) {
      console.warn(`⚠ ${file} sa nepodarilo prečítať: ${err.message}`);
    }
  }
}

if (!names.size) {
  console.log("Štýly nepoužívajú žiadne vzory – sprite zostáva bez zmeny.");
  process.exit(0);
}
console.log(`Vzory v štýloch (${names.size}): ${[...names].join(", ")}`);

/** Doplní vzory do jednej varianty spritu (1× alebo @2×). */
function addTo(suffix, pixelRatio) {
  const jsonPath = `${spriteBase}${suffix}.json`;
  const pngPath = `${spriteBase}${suffix}.png`;
  if (!existsSync(jsonPath) || !existsSync(pngPath)) return false;

  const index = JSON.parse(readFileSync(jsonPath, "utf8"));
  const atlas = decodePng(readFileSync(pngPath));

  // Existujúce obrázky vyberieme z atlasu, aby sa dal preskladať aj s novými.
  const boxes = [];
  for (const [name, e] of Object.entries(index)) {
    if (parsePatternName(name)) continue; // starý vzor – nahradí ho nový
    const data = Buffer.alloc(e.width * e.height * 4);
    for (let y = 0; y < e.height; y++) {
      for (let x = 0; x < e.width; x++) {
        const s = ((e.y + y) * atlas.width + e.x + x) * 4;
        atlas.data.copy(data, (y * e.width + x) * 4, s, s + 4);
      }
    }
    boxes.push({ name, width: e.width, height: e.height, data, entry: e });
  }

  for (const name of names) {
    const img = renderPattern(parsePatternName(name), pixelRatio);
    boxes.push({
      name,
      width: img.width,
      height: img.height,
      data: Buffer.from(img.data.buffer, img.data.byteOffset, img.data.length),
      entry: { pixelRatio }
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
      // ROZŤAHOVACIE PÁSMA SA MUSIA PRENIESŤ. Preskladanie atlasu mení len to,
      // KDE obrázok leží – čo o sebe hovorí, ostáva jeho. Štítok cesty
      // (`workers/assets/shields.mjs`) sa bez `stretchX`/`stretchY`/`content`
      // natiahne celý aj s rohmi a z obdĺžnika je pri dlhom čísle rozmazaná
      // kapsula; nič pri tom nespadne, lebo štýl aj sprite sú ďalej platné.
      ...(box.entry.stretchX ? { stretchX: box.entry.stretchX } : {}),
      ...(box.entry.stretchY ? { stretchY: box.entry.stretchY } : {}),
      ...(box.entry.content ? { content: box.entry.content } : {})
    };
  }

  writeFileSync(pngPath, encodePng({ ...packed, data: out }));
  writeFileSync(jsonPath, JSON.stringify(outIndex));
  console.log(
    `✓ ${jsonPath}: ${boxes.length} obrázkov (z toho ${names.size} vzorov), ` +
      `atlas ${packed.width}×${packed.height}`
  );
  return true;
}

if (!addTo("", 1)) {
  console.error(`::error::Sprite ${spriteBase}.json/.png neexistuje`);
  process.exit(1);
}
addTo("@2x", 2);
