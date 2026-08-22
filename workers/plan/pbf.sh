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
#   1. vezme PBF – z vlastnej URL, z rodičovského extraktu vyrezaného na
#      polygón kraja (`osmfr.parent`, rozpis pri `extract_from_parent`), alebo
#      z hotového osm.fr exportu kraja (keď súbor už leží z cache, nesťahuje
#      sa a ani nereže)
#   2. voliteľne ho oreže – `crop_bbox`, alebo štvorec rýchleho testu
#   3. vypíše `key`, `name`, `bbox`, `bboxkey` do $GITHUB_OUTPUT
#
# CHCE `data/region.poly` – bez neho sa kraj z rodiča vyrezať nedá a skript
# spadne späť na hotový extrakt (nahlas). Sťahuje ho `workers/plan/region-poly.py`
# v kroku, ktorý je PRED týmto; stráži to `workers/lint/pbf-parent.py`.
set -e

T0=$(date +%s)
mkdir -p data
CUSTOM_URL="$OPT_CUSTOM_PBF_URL"

# NA ČO TO PBF BUDE. `true` = kreslí sa z neho mapa, čiže Planetiler z neho
# stavia GEOMETRIU – a vtedy musí byť na hranici kraja úplné, lebo objekt
# s chýbajúcim uzlom alebo členskou cestou zahodí celý (rozpis pri
# `extract_from_parent`). `false` = čítajú sa z neho len TAGY (Build wiki
# hľadá odkazy na Wikipédiu), a tam je hotový extrakt kraja presne to správne:
# 373 MB rodiča by sa sťahovalo pre nič (pravidlo 7).
#
# Je to `env:` kroku, nie odhad zo skriptu – kto si pýta PBF, vie, načo mu je.
# Že hodnotu dostane každý volajúci a že pri `true` je krok s polygónom pred
# ním, stráži `workers/lint/pbf-parent.py`.
NEEDS_GEOMETRY="$PBF_NEEDS_GEOMETRY"

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

# Kraj vyrezaný z rodičovského PBF – prečo, hovorí rozpis nižšie pri volaní.
# $1 = kľúč rodiča v `workers/data/regions.json` (napr. `slovensko`).
# Vracia nenulový kód, keď to nevyšlo; volajúci vtedy siahne po hotovom
# extrakte kraja a povie nahlas, čo tým mapa stratí.
extract_from_parent() { # $1 = kľúč rodiča
  PDIR=$(jq -r --arg p "$1" '.[$p].osmfr.dir // ""' workers/data/regions.json)
  PSLUG=$(jq -r --arg p "$1" '.[$p].osmfr.slugs[0] // ""' workers/data/regions.json)
  PNAME=$(jq -r --arg p "$1" '.[$p].name // ""' workers/data/regions.json)
  if [ -z "$PDIR" ] || [ -z "$PSLUG" ]; then
    echo "::error::Rodič '$1' nie je vo workers/data/regions.json, alebo nemá \`osmfr.dir\` a \`osmfr.slugs\`. Oprav \`osmfr.parent\` regiónu '$KEY'."
    return 1
  fi
  # BEZ `.poly` SA REZAŤ NEDÁ a je to tá istá hranica, ktorou je orezaný
  # hotový extrakt z osm.fr – druhá definícia by sa s ňou raz rozišla
  # (pravidlo 1). Sťahuje ho krok `Polygón kraja`, ktorý je PRED týmto
  # (stráži `workers/lint/pbf-parent.py`).
  if [ ! -s data/region.poly ]; then
    echo "::warning::Polygón kraja (data/region.poly) tu ešte nie je, takže sa kraj nemá čím vyrezať."
    return 1
  fi

  # Rozpočet kroku: 373 MB Slovenska sa sťahovalo 15 s a `osmium extract
  # -s smart` bežal 42 s (dve jadrá, Bratislavský kraj, 37 MB von). Na
  # runneri počítaj do dvoch minút – a je to raz za deň, kľúč cache nesie
  # dátum.
  command -v osmium >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq osmium-tool; }
  echo "Kraj sa vyreže z rodiča $PNAME – tak bude celý aj to, čo prechádza cez jeho hranicu (~2 min)."
  if ! curl -fL --retry 3 --retry-delay 5 \
       -o data/parent.osm.pbf "$OSMFR_BASE/$PDIR/$PSLUG.osm.pbf"; then
    rm -f data/parent.osm.pbf
    return 1
  fi
  echo "  [1/2] rodič stiahnutý ($(du -h data/parent.osm.pbf | cut -f1)), režem na polygón kraja…"
  if ! osmium extract --overwrite -s smart -S types=multipolygon,boundary \
       --polygon data/region.poly \
       -o data/region-full.osm.pbf data/parent.osm.pbf; then
    rm -f data/parent.osm.pbf data/region-full.osm.pbf
    return 1
  fi
  mv data/region-full.osm.pbf data/region.osm.pbf
  rm -f data/parent.osm.pbf
  echo "  [2/2] hotovo ($(du -h data/region.osm.pbf | cut -f1))"
  # Čo z hranice ostalo neúplné, má byť VIDIEŤ – po tomto oreze to už nie sú
  # hranice krajov, ale štátna hranica (rodič je Slovensko). Informatívne,
  # preto `|| true`: mapa sa stavia aj tak.
  osmium check-refs -r data/region.osm.pbf || true
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
    # ŽIADNA RÚRA Z `osmium`: `head -1` (aj `sed …q`) zavrie rúru pod stále
    # píšucim producentom, ten dostane EPIPE a `pipefail` zhodí priradenie.
    # Výstup ide najprv do premennej, prvý riadok sa berie až z nej.
    BOXES=$(osmium fileinfo -g header.boxes data/region.osm.pbf)
    BBOX=$(head -1 <<<"$BOXES" | tr -d '() ')
  fi
  if [ -z "$BBOX" ]; then
    echo "::error::PBF nemá bbox v hlavičke – vyplň input custom_bbox (west,south,east,north)."
    exit 1
  fi
else
  # ----- prednastavený región z workers/data/regions.json -----
  KEY="$REGION"
  NAME=$(jq -r --arg r "$KEY" '.[$r].name' workers/data/regions.json)
  BBOX=$(jq -r --arg r "$KEY" '.[$r].bbox | join(",")' workers/data/regions.json)
  DIR=$(jq -r --arg r "$KEY" '.[$r].osmfr.dir' workers/data/regions.json)
  if [ "$NAME" = "null" ]; then echo "::error::Neznámy región: $KEY"; exit 1; fi

  OK=""
  # ÚPLNOSŤ NA HRANICI KRAJA – preto sa hotový extrakt kraja NEBERIE tak,
  # ako je, ale reže sa z rodičovského (`osmfr.parent`, teda Slovenska).
  #
  # Hotový `presovsky-latest.osm.pbf` z osm.fr je orezaný NA TVRDO: cesta,
  # rieka či les, ktorý pokračuje do vedľajšieho kraja, v ňom má uzly len po
  # hranicu a viacpolygónová plocha (`type=multipolygon`) časť členských ciest
  # vôbec. Planetiler z takého objektu geometriu nepostaví a ZAHODÍ HO CELÝ:
  #
  #     Error constructing line for OsmWay[…]: Missing location for node: …
  #     Error constructing polygon for OsmRelation[…]: error building multipolygon
  #
  # Namerané na Bratislavskom kraji (planetiler.jar z releases, maxzoom 12,
  # vlastná schéma na `highway`/`waterway`/`landuse`/`natural`):
  #
  #                                       osm.fr kraj    z rodiča `-s smart`
  #     zahodené čiary (way)                      97                       5
  #     zahodené plochy (way)                     66                       6
  #     zahodené plochy (multipolygon)            19                       1
  #     osmium check-refs: chýbajúce uzly      2 255                     252
  #     prvkov v dlaždiciach                 260 486                 262 377
  #
  # Nie je to teda „mapa presahuje za kraj" (to rieši `--polygon` a maska
  # v štýle), ale opak: v stiahnutom kraji CHÝBALO to, čo cez jeho hranicu
  # prechádza – a nechýbal len presah, chýbal celý objekt. Zvyšok (252 uzlov)
  # je na ŠTÁTNEJ hranici: rodič je Slovensko, takže les do Rakúska celý nikde
  # nie je. Na to by bola treba Európa (28 GB) a mapa tam aj tak končí.
  #
  # `-s smart` je „complete_ways + celé členské cesty relácií daných typov";
  # `types=multipolygon,boundary` je jeho predvolená hodnota, píše sa
  # naplno, lebo práve o ňu tu ide. Trasy (`type=route`) v nej ZÁMERNE nie sú:
  # ich značky si Planetiler prenáša na členské cesty, takže neúplná relácia
  # trasy nič nezahodí – a doplniť celú Cestu hrdinov SNP do kraja by znamenalo
  # ťahať cesty cez pol Slovenska.
  #
  # A robí sa to LEN pre PBF, z ktorého sa kreslí mapa (`PBF_NEEDS_GEOMETRY`,
  # viď hore) – Build wiki z neho číta iba tagy a tam by to bolo 373 MB pre nič.
  PARENT=$(jq -r --arg r "$KEY" '.[$r].osmfr.parent // ""' workers/data/regions.json)
  if [ -z "$CACHED" ] && [ -n "$PARENT" ] && [ "$NEEDS_GEOMETRY" = 'true' ]; then
    if extract_from_parent "$PARENT"; then
      OK=1
      FROM_PARENT=1
    else
      echo "::warning::Kraj sa nepodarilo vyrezať z rodičovského PBF ($PARENT) – beriem hotový extrakt kraja z osm.fr. Mapa sa postaví, ale objekty, ktoré prechádzajú cez hranicu kraja (cesty, rieky, veľké lesy a polia), v nej nebudú vôbec. Skús beh zopakovať."
    fi
  fi

  # Hotové osm.fr regionálne exporty (skúšaj kandidátske názvy súborov).
  # Sem sa ide, keď kraj rodiča nemá, keď PBF leží z cache (`download` vtedy
  # hneď vráti 0) a keď rez z rodiča nevyšiel – NIE po ňom: `curl -o` by
  # hotový, úplný súbor prepísalo tým dieravým.
  if [ -z "$OK" ]; then
    for SLUG in $(jq -r --arg r "$KEY" '.[$r].osmfr.slugs[]' workers/data/regions.json); do
      if download "$OSMFR_BASE/$DIR/$SLUG.osm.pbf"; then OK=1; break; fi
    done
  fi
  if [ -z "$OK" ]; then
    echo "::error::PBF pre '$KEY' sa nepodarilo stiahnuť. Obsah $OSMFR_BASE/$DIR/ (uprav slugs vo workers/data/regions.json):"
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

# RÝCHLY TEST (switch `test`, predvolene odškrtnutý, 4 km²) zmenšuje to,
# čo je naozaj drahé: vrstevnice, skaly a tieňovanie z výškového
# modelu – na kraji desiatky minút, na 4 km² jednotky minút.
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
  RES=$(python3 workers/plan/area.py \
    --region-bbox="$BBOX" --area="$AREA" \
    --test-km2="$TEST_KM2" \
    --test-at="$OPT_TEST_AT")
  DEM_BBOX=$(printf '%s\n' "$RES" | sed -n 's/^bbox=//p')
  [ -n "$DEM_BBOX" ] || { echo "::error::Testovací štvorec sa nepodarilo spočítať."; exit 1; }
  # Okolie pre obrázok „kde to je" je CELÝ výrez pred zmenšením
  # (napr. Vysoké Tatry), nie celý región – z mapy Slovenska by
  # bol štvorec so 4 km² neviditeľný bod.
  printf '%s\n' "$RES" | sed -n 's/^full_bbox=/full_bbox=/p' >> "$GITHUB_OUTPUT"
  echo "test_bbox=$DEM_BBOX" >> "$GITHUB_OUTPUT"
  # A ODLOŽ CELÚ ODPOVEĎ pre krok „Vyrieš testovací výrez": je v nej
  # už všetko, čo ten krok potrebuje – vrátane kľúča s príponou
  # `_test4`, ktorý mu druhým výpočtom vyjsť nemôže (prečo, hovorí
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
  "$NAME, $(du -h data/region.osm.pbf | cut -f1)$([ -n "$CACHED" ] && echo ' (z cache)')$([ -n "${FROM_PARENT:-}" ] && echo ' (vyrezaný z rodiča)')" \
  >> steps-out/plan.tsv
