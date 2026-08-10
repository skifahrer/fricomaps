#!/usr/bin/env bash
# Tieňovanie a 3D terén: terrarium PNG dlaždice z vybraného výškového modelu.
#
# Poradie je „najlacnejšie najprv": cache behu → sklad na Drive → prepočet.
# Hotové dlaždice sa ukladajú do skladu, takže ďalší beh nad tým istým
# regiónom, modelom a maxzoomom ich už len stiahne. (Do GitHub releasov sa
# nepublikuje nič – rozpis je vo `workers/drive/store.py`.)
#
# MENO ASSETU NESIE ZDROJ (`terrain-<kľúč>-<model>-z<maxzoom>.tar.zst`):
# tieňovanie zo Sonnyho a z DMR 3.5 nie je to isté a jedno sa nesmie vydávať
# za druhé – preto sa meno pri ústupe na Sonnyho prepočíta.
#
# Použitie (hodnoty chodia z prostredia, aby sa dal skript spustiť aj ručne):
#   REGION_KEY=presovsky_kraj DEM_BBOX=20,49,21,50 SHADING_SOURCE=sonny \
#   TERRAIN_MAXZOOM=13 TERRAIN_STORE=dem-terrain GDRIVE_CREDENTIALS=… \
#   workers/terrain/build.sh
set -euo pipefail
: "${REGION_KEY:?kľúč regiónu}"
T_TER=$(date +%s)
TSRC="výpočet"
FELL_BACK=false
TZ="${TERRAIN_MAXZOOM:-}"
case "$TZ" in ''|*[!0-9]*) TZ=13 ;; esac
# Tieňovanie je vrstva z DEM, takže ide na `dem_bbox` – pri rýchlom
# teste na testovací štvorec, nie na celý región.
BBOX="${DEM_BBOX:?bbox pre DEM}"
TDEM="${SHADING_SOURCE:?zdroj tieňovania}"
# Rozpočet na tieňovanie: podiel z rozpočtu celej stránky. Bez neho sa
# zmestenie riešilo až na konci behu, v kontrole pred nasadením – čiže po
# celej práci. Jemný model (DMR 5.0 → z15) je pritom nad celým krajom
# šestnásťkrát viac dlaždíc než z13, tak to musí vedieť ten, kto ich robí.
LIMIT_MB="${SIZE_LIMIT_MB:-900}"
case "$LIMIT_MB" in ''|*[!0-9]*) LIMIT_MB=900 ;; esac
TPCT="${BUDGET_TERRAIN_PCT:-12}"
case "$TPCT" in ''|*[!0-9]*) TPCT=12 ;; esac
TBUDGET_MB=$(( LIMIT_MB * TPCT / 100 ))
REBUILD="${TERRAIN_REBUILD:-false}"

# Meno assetu nesie ZDROJ a MAXZOOM – tieňovanie zo Sonnyho a z DMR 3.5 nie
# je to isté a z13 nie je z15. Skutočný maxzoom je ale známy až po výpočte
# (strop veľkosti ho môže zraziť), takže sa meno skladá funkciou a volá sa
# dvakrát: raz s tým želaným, keď sa hľadá v sklade, a raz s vyrobeným,
# keď sa nahráva.
asset_name() { echo "terrain-${REGION_KEY}-${TDEM}-z${1}.tar.zst"; }

have_tiles() { [ -d terrain-out ] && [ -n "$(ls -A terrain-out 2>/dev/null)" ]; }

if [ "$REBUILD" = 'true' ]; then
  echo "terrain_rebuild=áno – dlaždice sa počítajú nanovo."
  rm -rf terrain-out
elif have_tiles; then
  echo "Výškové dlaždice sú v cache behu ✓"
  TSRC="cache"
else
  # Skús sklad – uložené dlaždice sú lacnejšie než ich prepočítať. Hľadá sa
  # NAJVYŠŠÍ uložený zoom, ktorý nie je vyšší než želaný: keď minulý beh
  # narazil na strop veľkosti a uložil z13, je to presne to, čo by sa znova
  # vypočítalo – a hľadanie na presné meno „z15" by ho nenašlo a hodinu by
  # počítalo to isté. Nižší zoom je platná odpoveď, vyšší nie (ten by dal
  # jemnejšie dlaždice, než sa žiadalo, a stránka by sa nemusela zmestiť).
  HAVE_Z=$(python3 workers/drive/store.py --names --store="$TERRAIN_STORE" \
      2>/dev/null \
    | sed -n "s/^terrain-${REGION_KEY}-${TDEM}-z\([0-9]\+\)\.tar\.zst$/\1/p" \
    | awk -v want="$TZ" '$1 <= want' | sort -n | tail -1)
  if [ -n "$HAVE_Z" ] && python3 workers/drive/store.py --get \
       --store="$TERRAIN_STORE" --name="$(asset_name "$HAVE_Z")" --dir=/tmp; then
    mkdir -p terrain-out
    tar --use-compress-program=unzstd -xf "/tmp/$(asset_name "$HAVE_Z")" \
      -C terrain-out
    echo "Výškové dlaždice stiahnuté zo skladu $TERRAIN_STORE ✓ (z$HAVE_Z)"
    TSRC="sklad $TERRAIN_STORE"
  fi
fi

if ! have_tiles; then
  # Vlastný job = vlastný DEM. Vrstevnice si ho sťahujú tiež, ale
  # bežia súbežne, takže sa oň nedá oprieť; skript je spoločný.
  sudo apt-get update -qq
  sudo apt-get install -y -qq gdal-bin zstd
  python3 -m pip install --quiet numpy
  # Model z výberu `shading_source`. Do podpriečinka podľa zdroja –
  # rovnako ako v jobe s vrstevnicami, nech sa dve rôzne mozaiky
  # nikdy neprebijú v jednom `all.vrt`. Kľúč výrezu sa nepodáva, tak
  # `dmr5` vyjde na dlaždicovú 5 m verziu – tieňovanie sa robí vždy
  # na celý región.
  #
  # KÓD 3 = „ten model pre toto územie nemáme". Vrstevnice to vedeli
  # ustáť odjakživa, tieňovanie nie – a beh 31307163093 preto
  # sčervenel na poslednom jobe, hoci mapa sa nasadila. Rovnaký
  # fallback ako pri vrstevniciach, riadený tým istým prepínačom.
  set +e
  workers/dem/fetch.sh "$BBOX" "dem/$TDEM" steps-out/terrain.tsv "$TDEM"
  TRC=$?
  set -e
  if [ "$TRC" -eq 3 ]; then
    if [ "${OPT_UGKK_FALLBACK:-true}" != 'true' ]; then
      echo "::error::Model $TDEM pre tieňovanie nie je k dispozícii a ugkk_fallback je vypnutý. Naplň ho, zapni fallback, alebo vyber iný shading_source."
      exit 1
    fi
    echo "::warning::Model $TDEM pre tieňovanie nie je k dispozícii – tieňovanie sa počíta zo Sonnyho (20 m). Mapa bude, len s hrubším reliéfom, a atribúcia bude hovoriť Sonny."
    TDEM=sonny
    FELL_BACK=true
    # Meno súboru nesie zdroj – `asset_name` ho skladá z premennej `TDEM`,
    # takže sa prepísaním modelu opraví samo. Kým to bol reťazec napísaný
    # druhýkrát, uložili sa Sonnyho dlaždice pod menom toho druhého modelu
    # a nabudúce sa vydávali za neho.
    workers/dem/fetch.sh "$BBOX" "dem/$TDEM" steps-out/terrain.tsv "$TDEM"
  elif [ "$TRC" -ne 0 ]; then
    exit "$TRC"
  fi
  echo "::group::Výškové dlaždice do z$TZ z modelu $TDEM (strop ${TBUDGET_MB} MB)"
  python3 workers/terrain/tiles.py --dem="dem/$TDEM/all.vrt" --bbox="$BBOX" \
    --minzoom=5 --maxzoom="$TZ" --budget-mb="$TBUDGET_MB" --out=terrain-out
  # Vyrobený maxzoom píše `build-terrain.py` – strop veľkosti mohol ten
  # želaný zraziť a asset sa MUSÍ volať podľa toho, čo v ňom naozaj je.
  TZ=$(cat terrain-out/maxzoom.txt)
  echo "::endgroup::"

  # Ulož do skladu, nech ich nabudúce netreba počítať znova. Zlyhanie
  # uloženia NESMIE zhodiť beh – dlaždice sú spočítané a v `terrain-out`,
  # takže mapa bude; stratí sa len to, že sa nabudúce budú počítať znova.
  ASSET=$(asset_name "$TZ")
  tar --use-compress-program='zstd -19 -T0' -cf "/tmp/$ASSET" -C terrain-out .
  python3 workers/drive/store.py --put --store="$TERRAIN_STORE" \
      --file="/tmp/$ASSET" \
      --note="Terrarium PNG dlaždice z výškového modelu – jeden .tar.zst na región, model a maxzoom (Build map)" \
    && echo "Uložené do skladu $TERRAIN_STORE ako $ASSET" \
    || echo "::warning::Výškové dlaždice sa nepodarilo uložiť do skladu $TERRAIN_STORE – nabudúce sa budú počítať znova."
fi

TZ=$(cat terrain-out/maxzoom.txt 2>/dev/null || echo "$TZ")
mkdir -p _site/terrain
find terrain-out -mindepth 1 -maxdepth 1 -type d -exec cp -r {} _site/terrain/ \;
echo "enabled=true" >> "$GITHUB_OUTPUT"
echo "maxzoom=$TZ" >> "$GITHUB_OUTPUT"
# Model ide do atribúcie výškových dlaždíc v štýle. Odkedy má
# tieňovanie vlastný výber, nemusí to byť ten istý model ako
# pri vrstevniciach – a mapa nesmie tvrdiť cudzí zdroj.
echo "dem_source=$TDEM" >> "$GITHUB_OUTPUT"
# Keď sa spadlo na Sonnyho, dlaždice sa NESMÚ uložiť pod kľúč cache
# toho pôvodného modelu – kľúč nesie jeho meno a nabudúce by sa
# z neho vrátili ako keby boli jeho. Do skladu ísť môžu, tam ich
# meno súboru už hovorí pravdu.
echo "fell_back=$FELL_BACK" >> "$GITHUB_OUTPUT"
echo "Výškové dlaždice: $(find _site/terrain -name '*.png' | wc -l) ks do z$TZ z modelu $TDEM, $(du -sm _site/terrain | cut -f1) MB"
printf '%s\t%s\t%s\t%s\n' "60" "Tieňovanie a 3D terén" "$(( $(date +%s) - T_TER ))" \
  "$(find _site/terrain -name '*.png' | wc -l) PNG dlaždíc do z$TZ z $TDEM, $(du -sm _site/terrain | cut -f1) MB ($TSRC)" \
  >> steps-out/terrain.tsv
