#!/usr/bin/env bash
# Smoke test NASADENEJ mapy: nie „vyrobili sme súbory", ale „sú na webe
# a dajú sa prečítať tak, ako ich mapa číta".
#
# PMTiles sa číta HTTP Range requestmi, takže sa nekontroluje 200, ale **206**.
# Server, ktorý Range nevie, vráti na tú istú adresu 200 a celý súbor – mapa
# sa potom nenačíta a zo samotnej existencie súboru sa to nepozná.
#
# Použitie (hodnoty chodia z prostredia, aby sa dal skript spustiť aj ručne):
#   BASE=https://user.github.io/repo REGION=presovsky_kraj SPRITE=osm-liberty \
#   workers/smoke-test.sh
set -uo pipefail

BASE="${BASE:?adresa nasadenej stránky}"
BASE="${BASE%/}"
REGION="${REGION:?kľúč regiónu (plan.outputs.key)}"
SPRITE="${SPRITE:?meno spritu (assets.outputs.name)}"
PAGES_BUILD_TYPE="${PAGES_BUILD_TYPE:-}"

fail=0

check() { # $1 = URL, $2 = očakávaný kód, $3 = popis, $4 = extra curl args
  local code=000 attempt
  for attempt in 1 2 3 4 5; do
    # shellcheck disable=SC2086  # $4 sú zámerne rozdelené argumenty curlu
    code=$(curl -s -o /dev/null -w '%{http_code}' ${4:-} "$1" || echo 000)
    [ "$code" = "$2" ] && break
    sleep $(( attempt * 5 ))
  done
  if [ "$code" = "$2" ]; then
    echo "  ✓ $3 ($code)"
  else
    echo "::error::$3 vrátilo HTTP $code (očakávané $2) – $1"
    fail=1
  fi
}

echo "Kontrolujem $BASE"
check "$BASE/tiles/manifest.json" 200 "manifest.json"
check "$BASE/sprites/$SPRITE.json" 200 "sprite index"
check "$BASE/sprites/$SPRITE.png" 200 "sprite bitmapa"
check "$BASE/styles/$REGION-svetla.json" 200 "style.json"
# Typy máp: každý má vlastný štýl pre každú tému; overíme jeden iný
# než predvolený, nech sa nestane, že sa nasadí len ten starý názov.
check "$BASE/styles/$REGION-cestna-svetla.json" 200 "style.json (cestná mapa)"
check "$BASE/style-overrides.json" 200 "úpravy štýlu z developer módu"

GLYPHS=$(curl -s "$BASE/tiles/manifest.json" | jq -r '.glyphs')
case "$GLYPHS" in
  "$BASE"*) check "$BASE/fonts/Noto%20Sans%20Regular/0-255.pbf" 200 "glyfy" ;;
  *) echo "  ℹ glyfy sú externé: $GLYPHS" ;;
esac

# Základné dlaždice sú vždy; ostatné vrstvy len keď ich beh naozaj vyrobil.
# Prázdna premenná = vrstva sa nepočítala, tak sa ani nekontroluje.
check "$BASE/tiles/$REGION.pmtiles" 206 "pmtiles (Range request)" "-H Range:bytes=0-1023"
for pair in "${CONTOURS:-}:contours:vrstevnice" \
            "${ROCKS:-}:rocks:skaly" \
            "${TRAILS:-}:trails:značené trasy" \
            "${FEATURES:-}:features:krajinné prvky"; do
  IFS=: read -r on src popis <<<"$pair"
  [ "$on" = 'true' ] || continue
  check "$BASE/tiles/$REGION-$src.pmtiles" 206 "$popis (Range request)" \
        "-H Range:bytes=0-1023"
done

# Na koreni stránky má byť MAPA. Všetko vyššie kontroluje súbory, ktoré
# README nemá – ale práve preto by prepísanú stránku nechytilo, keby tam
# ostali z predošlého nasadenia. Toto je tá jediná otázka, na ktorej
# návštevníkovi záleží: čo vidí, keď otvorí adresu. Jekyll z README vyrobí
# stránku bez `id="map"`.
if curl -sL "$BASE/" | grep -q 'id="map"'; then
  echo "  ✓ na koreni stránky je mapa"
elif [ "$PAGES_BUILD_TYPE" != 'workflow' ]; then
  # Príčinu poznáme z prvého kroku a opraviť sa dá len v nastaveniach
  # repozitára – červený beh by k tomu nič nepridal.
  echo "::warning::Na $BASE/ nie je mapa, ale README: zdroj Pages je vetva (build_type=$PAGES_BUILD_TYPE), takže obsah prepísal zabudovaný Jekyll builder. Settings → Pages → Source: 'GitHub Actions'."
else
  echo "::error::Na $BASE/ nie je mapa, hoci zdroj Pages je Actions – niečo je inak, než čakáme. Pozri, čo sa nasadilo."
  fail=1
fi

exit $fail
