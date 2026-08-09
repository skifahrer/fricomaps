#!/usr/bin/env bash
# PBF regiónu na disk – stiahnutie, prípadné orezanie, kľúč a bbox pre build.
#
# PREČO SAMOSTATNÝ SKRIPT A NIE `run:` V WORKFLOWE: `build-map.yml` má strop
# 128 kB, nad ktorým ho GitHub NEPRIJME – a nepovie to; po pushi len vyrobí beh
# bez jobov s červeným krížikom a prázdnym logom. Toto bol jeho najväčší `run:`
# blok (128 riadkov). Bokom od toho je to aj tak správnejšie: takto sa dá
# spustiť lokálne a nie „pushni a pozri sa, čo z toho vyšlo".
#
# `set -e` BEZ `-u` A BEZ `pipefail` je zámer, nie nedbalosť: presne v tomto
# režime tento kód bežal, kým bol v YAMLe (predvolený shell kroku je
# `bash -e {0}`), a presun do súboru to nemá meniť. Sťahovanie skúša viac ciest
# a spolieha sa, že neúspešná vetva len vráti nenulový kód.
#
# ČO ROBÍ, V PORADÍ:
#   1. vezme PBF – z vlastnej URL, alebo z osm.fr exportov podľa regiónu
#      (keď súbor už leží z cache, nesťahuje sa a ani nereže)
#   2. voliteľne ho oreže – `crop_bbox`, alebo štvorec rýchleho testu
#   3. vypíše `key`, `name`, `bbox`, `bboxkey` do $GITHUB_OUTPUT
set -e

T0=$(date +%s)
mkdir -p data
CUSTOM_URL="$OPT_CUSTOM_PBF_URL"

# Cache: keď PBF (už aj orezané) leží z predošlého behu, sťahovať ani
# rezať netreba – kľúč nesie región, orez aj dátum.
CACHED=""
if [ -s data/region.osm.pbf ]; then
  CACHED=1
  echo "PBF z cache ✓ ($(du -h data/region.osm.pbf | cut -f1))"
fi

download() { # $1 = URL
  [ -n "$CACHED" ] && return 0
  echo "Skúšam: $1"
  curl -fL --retry 3 --retry-delay 5 -o data/region.osm.pbf "$1"
}

if [ -n "$CUSTOM_URL" ]; then
  # ----- vlastný región (Európa / svet) -----
  NAME="$OPT_CUSTOM_NAME"
  [ -n "$NAME" ] || NAME=$(basename "$CUSTOM_URL" .osm.pbf)
  KEY=$(echo "$NAME" | LC_ALL=C.UTF-8 iconv -f utf8 -t ascii//TRANSLIT | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_' | sed 's/^_//;s/_*$//')
  download "$CUSTOM_URL" || { echo "::error::Nepodarilo sa stiahnuť $CUSTOM_URL"; exit 1; }

  BBOX="$OPT_CUSTOM_BBOX"
  if [ -z "$BBOX" ]; then
    sudo apt-get update -qq && sudo apt-get install -y -qq osmium-tool
    BBOX=$(osmium fileinfo -g header.boxes data/region.osm.pbf | head -1 | tr -d '() ')
  fi
  if [ -z "$BBOX" ]; then
    echo "::error::PBF nemá bbox v hlavičke – vyplň input custom_bbox (west,south,east,north)."
    exit 1
  fi
else
  # ----- prednastavený región z workers/regions.json -----
  KEY="$REGION"
  NAME=$(jq -r --arg r "$KEY" '.[$r].name' workers/regions.json)
  BBOX=$(jq -r --arg r "$KEY" '.[$r].bbox | join(",")' workers/regions.json)
  DIR=$(jq -r --arg r "$KEY" '.[$r].osmfr.dir' workers/regions.json)
  if [ "$NAME" = "null" ]; then echo "::error::Neznámy región: $KEY"; exit 1; fi

  OK=""
  # osm.fr regionálne exporty (skúšaj kandidátske názvy súborov)
  for SLUG in $(jq -r --arg r "$KEY" '.[$r].osmfr.slugs[]' workers/regions.json); do
    if download "$OSMFR_BASE/$DIR/$SLUG.osm.pbf"; then OK=1; break; fi
  done
  if [ -z "$OK" ]; then
    echo "::error::PBF pre '$KEY' sa nepodarilo stiahnuť. Obsah $OSMFR_BASE/$DIR/ (uprav slugs vo workers/regions.json):"
    curl -sL "$OSMFR_BASE/$DIR/" | grep -oE 'href="[^"]+\.osm\.pbf"' | sort -u || true
    echo "…alebo vyplň custom_pbf_url s priamou URL na .osm.pbf."
    exit 1
  fi
fi

# ----- voliteľné orezanie na menšie územie -----
# Menšie územie = výrazne menší .pmtiles, takže sa dá ísť na maxzoom 16.
# Toto orezáva PBF, čiže samotnú mapu – na rozdiel od rýchleho testu
# o kus nižšie, ktorý mapu necháva celú.
CROP="$OPT_CROP_BBOX"
if [ -n "$CROP" ]; then
  if [ -z "$CACHED" ]; then
    command -v osmium >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq osmium-tool; }
    echo "Orezávam na bbox $CROP …"
    if ! osmium extract --overwrite -b "$CROP" -s smart \
         -o data/region-crop.osm.pbf data/region.osm.pbf; then
      echo "::error::Orezanie na bbox '$CROP' zlyhalo – očakávaný formát je west,south,east,north (napr. 18.98,49.18,19.20,49.28)."
      exit 1
    fi
    mv data/region-crop.osm.pbf data/region.osm.pbf
  fi
  BBOX="$CROP"
  KEY="${KEY}_crop"
  NAME="$NAME (výrez)"
fi

# RÝCHLY TEST (switch `test`, predvolene zapnutý, 2 km²) zmenšuje to,
# čo je naozaj drahé: vrstevnice, skaly a tieňovanie z výškového
# modelu – na kraji desiatky minút, na 2 km² sekundy.
#
# REGIÓN SA PRITOM NEOREZÁVA: mapa (cesty, vodstvo, trasy, prvky)
# vyjde celá podľa nastavení, prešovský kraj ostane prešovským
# krajom. Planetiler z PBF kraja je pár minút, takže sa tým nič
# neladí pomalšie – zato sa štvorec so skalami pozerá v mape, do
# ktorej patrí, a nie nad prázdnom.
TEST_KM2="$OPT_TEST_KM2"
DEM_BBOX="$BBOX"
if [ "${TEST_KM2:-0}" != "0" ]; then
  AREA="$AREA_IN"
  AREA_BBOX="$OPT_AREA_BBOX"
  [ -n "$AREA_BBOX" ] && AREA="$AREA_BBOX"
  RES=$(python3 workers/resolve-area.py \
    --region-bbox="$BBOX" --area="$AREA" \
    --test-km2="$TEST_KM2" \
    --test-at="$OPT_TEST_AT")
  DEM_BBOX=$(printf '%s\n' "$RES" | sed -n 's/^bbox=//p')
  [ -n "$DEM_BBOX" ] || { echo "::error::Testovací štvorec sa nepodarilo spočítať."; exit 1; }
  # Okolie pre obrázok „kde to je" je CELÝ výrez pred zmenšením
  # (napr. Vysoké Tatry), nie celý región – z mapy Slovenska by
  # bol štvorec s 2 km² neviditeľný bod.
  printf '%s\n' "$RES" | sed -n 's/^full_bbox=/full_bbox=/p' >> "$GITHUB_OUTPUT"
  echo "test_bbox=$DEM_BBOX" >> "$GITHUB_OUTPUT"
  # A ODLOŽ CELÚ ODPOVEĎ pre krok „Vyrieš testovací výrez": je v nej
  # už všetko, čo ten krok potrebuje – vrátane kľúča s príponou
  # `_test2`, ktorý mu druhým výpočtom vyjsť nemôže (prečo, hovorí
  # komentár v tom kroku). Rátať sa to má RAZ.
  printf '%s\n' "$RES" > /tmp/vyrez.txt
  # Kľúč ide do mien cache aj uložených výsledkov – testovací beh sa
  # nesmie tváriť ako ostrý a prepísať mu vrstevnice na celý kraj.
  KEY="${KEY}_test${TEST_KM2}"
  NAME="$NAME – test ${TEST_KM2} km²"
  echo "Testovací režim: $TEST_KM2 km² → $DEM_BBOX (mapa ostáva celý región $BBOX)"
fi

echo "key=$KEY"   >> "$GITHUB_OUTPUT"
echo "name=$NAME" >> "$GITHUB_OUTPUT"
echo "bbox=$BBOX" >> "$GITHUB_OUTPUT"
echo "dem_bbox=$DEM_BBOX" >> "$GITHUB_OUTPUT"
# bezpečná podoba bboxu do kľúča cache. Je z `dem_bbox`, lebo ho
# používajú len cache vrstevníc, skál a tieňovania – a tie sa pri
# teste počítajú na štvorci, takže si nesmú sadnúť na ostré.
echo "dem_bboxkey=$(echo "$DEM_BBOX" | tr ',.-' '___')" >> "$GITHUB_OUTPUT"
echo "Región: $NAME (key=$KEY, bbox=$BBOX)"
ls -lh data/region.osm.pbf
printf '%s\t%s\t%s\t%s\n' "10" "PBF regiónu" "$(( $(date +%s) - T0 ))" \
  "$NAME, $(du -h data/region.osm.pbf | cut -f1)$([ -n "$CACHED" ] && echo ' (z cache)')" \
  >> steps-out/plan.tsv
