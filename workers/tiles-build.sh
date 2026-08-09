#!/usr/bin/env bash
# PBF → `{región}.pmtiles` Planetilerom, s rozpočtom na veľkosť.
#
# PREČO SAMOSTATNÝ SKRIPT: `build-map.yml` má strop 128 kB a nad ním ho GitHub
# ticho neprijme (stráži to Lint workflows).
#
# ROZPOČET JE NA CELÚ STRÁNKU, nielen na tieto dlaždice: Pages zvládne ~1 GB
# a do toho sa musia zmestiť aj vrstevnice, terén, fonty a sprity. Tie sa ale
# počítajú v INÝCH JOBOCH a súbežne s týmto, takže tu ešte nikto nevie, aké
# budú veľké. Namiesto „čo zvýšilo" preto dostanú dlaždice pevný podiel
# (`BUDGET_*_PCT` v env workflowu) a job `deploy` na konci overí, že súčet
# naozaj sedí.
#
# Keď sa výsledok nezmestí a `auto_shrink` je zapnutý, zoom sa zníži a beží sa
# znova – preto je to slučka a nie jeden priechod.

set -euo pipefail
T_TILES=$(date +%s)
mkdir -p _site/tiles
OUT="_site/tiles/${REGION_KEY}.pmtiles"

# Planetiler: PlanetilerConfig.MAX_MAXZOOM = 16 → vyššie hodnoty
# zhodia build s "Max zoom must be <= 16". Radšej orežeme s hláškou.
MAXZOOM="$OPT_MAXZOOM"
case "$MAXZOOM" in ''|*[!0-9]*) MAXZOOM=16 ;; esac
if [ "$MAXZOOM" -gt 16 ]; then
  echo "::warning::Planetiler vie vygenerovať dlaždice najviac po zoom 16 (zadané: $MAXZOOM). Používam 16 – priblíženie až na z20 zabezpečí overzoom v MapLibre."
  MAXZOOM=16
fi
if [ "$MAXZOOM" -lt 8 ]; then MAXZOOM=8; fi

# Rozpočet je na CELÚ stránku, nielen na tieto dlaždice: Pages
# zvládne ~1 GB a do toho sa musia zmestiť aj vrstevnice, terén,
# fonty a sprity. Tie sa ale počítajú v INÝCH JOBOCH a súbežne
# s týmto, takže tu ešte nikto nevie, aké budú veľké. Namiesto
# „čo zvýšilo" preto dostanú dlaždice pevný podiel a job `deploy`
# na konci overí, že súčet naozaj sedí.
LIMIT_MB="$SIZE_LIMIT_MB"
case "$LIMIT_MB" in ''|*[!0-9]*) LIMIT_MB=900 ;; esac
OTHERS_MB=$(( LIMIT_MB * (BUDGET_CONTOURS_PCT + BUDGET_TERRAIN_PCT + BUDGET_TRAILS_PCT + BUDGET_FEATURES_PCT) / 100 + BUDGET_ASSETS_MB ))
BUDGET_MB=$(( LIMIT_MB - OTHERS_MB ))
echo "Rozpočet stránky ${LIMIT_MB} MB − vrstevnice ${BUDGET_CONTOURS_PCT} % − terén ${BUDGET_TERRAIN_PCT} % − trasy ${BUDGET_TRAILS_PCT} % − krajinné prvky ${BUDGET_FEATURES_PCT} % − ikonky a fonty ${BUDGET_ASSETS_MB} MB = ${BUDGET_MB} MB na dlaždice"

if [ "$BUDGET_MB" -lt 50 ]; then
  echo "::error::Na dlaždice zostáva len ${BUDGET_MB} MB. Zdvihni size_limit_mb alebo uber podiel vrstevniciam a terénu (BUDGET_*_PCT v env)."
  exit 1
fi
LIMIT=$(( BUDGET_MB * 1024 * 1024 ))

Z=$MAXZOOM
while : ; do
  echo "::group::Planetiler – maxzoom $Z"
  java -Xmx5g -jar planetiler.jar \
    --osm-path=data/region.osm.pbf \
    --output="$OUT" \
    --download --download-dir=data/sources \
    --minzoom=0 \
    --maxzoom="$Z" \
    --render_maxzoom="$Z" \
    --min_feature_size_at_max_zoom=0 \
    --simplify_tolerance_at_max_zoom=0 \
    --transportation_z13_paths=true \
    --building_merge_z13=false \
    --languages=sk,en \
    --force
  echo "::endgroup::"

  BYTES=$(stat -c%s "$OUT")
  MB=$(( BYTES / 1048576 ))
  echo "maxzoom $Z → ${MB} MB (na dlaždice je ${BUDGET_MB} MB)"

  if [ "$BYTES" -le "$LIMIT" ]; then break; fi

  if [ "$OPT_AUTO_SHRINK" != 'true' ] || [ "$Z" -le 12 ]; then
    echo "::error::Dlaždice majú ${MB} MB, ale majú sa zmestiť do ${BUDGET_MB} MB (rozpočet stránky ${LIMIT_MB} MB mínus podiel vrstevníc, terénu a ikoniek). Možnosti: zníž maxzoom, vyber menší región, použi crop_bbox alebo uber vrstevniciam cez BUDGET_CONTOURS_PCT."
    exit 1
  fi

  # O koľko zoomov ísť dolu: každý nižší zoom zmenší dlaždice zhruba
  # 3,5×. Skákať po jednom by pri celom Slovensku znamenalo aj tri
  # hodinové behy Planetileru za sebou; naraz ale ideme najviac o dva,
  # nech sa detail nezahodí zbytočne.
  DROP=1
  EST=$MB
  while [ "$DROP" -lt 2 ] && [ $(( EST * 10 / 35 )) -gt "$BUDGET_MB" ]; do
    EST=$(( EST * 10 / 35 ))
    DROP=$(( DROP + 1 ))
  done
  NEXT=$(( Z - DROP ))
  if [ "$NEXT" -lt 12 ]; then NEXT=12; fi
  echo "::warning::${MB} MB je nad rozpočtom ${BUDGET_MB} MB – skúšam maxzoom ${NEXT}."
  Z=$NEXT
done

echo "maxzoom=$Z" >> "$GITHUB_OUTPUT"
echo "size_mb=$(( $(stat -c%s "$OUT") / 1048576 ))" >> "$GITHUB_OUTPUT"
ls -lh _site/tiles/
printf '%s\t%s\t%s\t%s\n' "70" "Mapové dlaždice (Planetiler)" "$(( $(date +%s) - T_TILES ))" \
  "maxzoom $Z, $(( $(stat -c%s "$OUT") / 1048576 )) MB" \
  >> steps-out/tiles.tsv
