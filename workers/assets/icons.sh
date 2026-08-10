#!/usr/bin/env bash
# SDF sprity zo sád ikoniek → `_site/sprites/`.
#
# PREČO SAMOSTATNÝ SKRIPT: `build-map.yml` má strop 128 kB a nad ním ho GitHub
# ticho neprijme (stráži to Lint workflows).
#
# ZOZNAM ZDROJOV JE V `poc/web/icon-sources.js` – jedno miesto pre web aj
# pipeline. Z každého sa vyrobí SDF sprite: symboly bez koliesok a podkladov,
# ktorým sa dá nastaviť farba.
#
# JEDNA SADA, KTORÁ SA NESTIAHNE, NIE JE DÔVOD ZHODIŤ BUILD – sú to súbory
# z cudzích serverov. Preskočí sa s varovaním; chyba je až to, keď nevyjde ani
# jedna. Ktoré sady naozaj vznikli, ide von v `available`, aby manifest
# neponúkal prepínač na prázdny sprite.
#
# `set -uo pipefail` bez `-e` je zámer: presne v tomto režime tento kód bežal,
# kým bol v YAMLe, a preskakovanie chýbajúcich sád na tom stojí.

set -uo pipefail
T_SPR=$(date +%s)
mkdir -p _site/sprites /tmp/icons

# Cache: hotové sprity sa nemenia, kým sa nezmení zoznam zdrojov ani
# generátor – v kľúči je hash oboch.
# find, nie ls: `ls vzor*` bez zhody končí kódom 2 a `pipefail`
# by ním zhodil celý krok (find nad existujúcim adresárom vráti 0).
CACHED_SPRITES=$(find _site/sprites -maxdepth 1 -name '*.json' | wc -l)

# Zoznam zdrojov je v poc/web/icon-sources.js – jedno miesto pre web
# aj pipeline. Z každého sa vyrobí SDF sprite: symboly bez koliesok
# a podkladov, ktorým sa dá nastaviť farba.
node -e "
  import('./poc/web/icon-sources.js').then((m) => {
    for (const s of m.ICON_SOURCES) console.log(s.id + ' ' + s.sprite);
  });
" > /tmp/icons/list.txt
cat /tmp/icons/list.txt

ok=""
while read -r id url; do
  [ -n "$id" ] || continue
  if [ "$CACHED_SPRITES" -gt 0 ] && [ -s "_site/sprites/$id.json" ]; then
    echo "── $id (z cache)"
    ok="$ok $id"
    continue
  fi
  echo "── $id"
  got=1
  for ext in .json .png; do
    curl -fL --retry 4 --retry-delay 5 -o "/tmp/icons/$id$ext" "$url$ext" || got=0
  done
  # @2x je voliteľné – bez neho mapa funguje, len je na retine mäkšia.
  for ext in '@2x.json' '@2x.png'; do
    curl -fL --retry 2 --retry-delay 3 -o "/tmp/icons/$id$ext" "$url$ext" \
      || rm -f "/tmp/icons/$id$ext"
  done
  if [ "$got" != 1 ]; then
    echo "::warning::Sadu ikoniek $id sa nepodarilo stiahnuť – preskakujem."
    continue
  fi
  if node workers/assets/sprite.mjs --in="/tmp/icons/$id" --out="_site/sprites/$id"; then
    ok="$ok $id"
  else
    echo "::warning::Sadu ikoniek $id sa nepodarilo prerobiť na SDF – preskakujem."
  fi
done < /tmp/icons/list.txt

if [ -z "$ok" ]; then
  echo "::error::Nepodarilo sa pripraviť ani jednu sadu ikoniek – mapa by bola bez ikon."
  exit 1
fi

# Ktorú sadu má použiť štýl, hovoria úpravy z developer módu.
WANT=$(node -e "
  Promise.all([import('./poc/web/themes.js'), import('node:fs')]).then(([m, fs]) => {
    let raw = {};
    try { raw = JSON.parse(fs.readFileSync('poc/web/style-overrides.json', 'utf8')); } catch {}
    console.log(m.selectedIconSource(m.normalizeOverrides(raw).overrides));
  });
")
if [ ! -s "_site/sprites/$WANT.json" ]; then
  WANT=$(printf '%s' "$ok" | awk '{print $1}')
  echo "::warning::Zvolená sada ikoniek nie je k dispozícii – používam $WANT."
fi
echo "name=$WANT" >> "$GITHUB_OUTPUT"
echo "available=$(printf '%s' "$ok" | xargs)" >> "$GITHUB_OUTPUT"
echo "Nasadené sady:$ok, štýl použije $WANT"
printf '%s\t%s\t%s\t%s\n' "80" "Ikonky (SDF sprity)" "$(( $(date +%s) - T_SPR ))" \
  "sady:$ok, štýl používa $WANT$([ "$CACHED_SPRITES" -gt 0 ] && echo ' (z cache)')" \
  >> steps-out/assets.tsv
