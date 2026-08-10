#!/usr/bin/env bash
# Tieňovanie a 3D terén: terrarium PNG dlaždice z vybraného výškového modelu.
#
# Poradie je „najlacnejšie najprv": cache behu → sklad na Drive → prepočet.
# Hotové dlaždice sa ukladajú do skladu, takže ďalší beh nad tým istým
# regiónom, modelom a maxzoomom ich už len stiahne. (Do GitHub releasov sa
# nepublikuje nič – rozpis je vo `workers/drive-store.py`.)
#
# MENO ASSETU NESIE ZDROJ (`terrain-<kľúč>-<model>-z<maxzoom>.tar.zst`):
# tieňovanie zo Sonnyho a z DMR 3.5 nie je to isté a jedno sa nesmie vydávať
# za druhé – preto sa meno pri ústupe na Sonnyho prepočíta.
#
# Použitie (hodnoty chodia z prostredia, aby sa dal skript spustiť aj ručne):
#   REGION_KEY=presovsky_kraj DEM_BBOX=20,49,21,50 SHADING_SOURCE=sonny \
#   TERRAIN_MAXZOOM=13 TERRAIN_STORE=dem-terrain GDRIVE_CREDENTIALS=… \
#   workers/terrain-build.sh
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
# Zdroj je v mene assetu: tieňovanie zo Sonnyho a z DMR 3.5 nie je
# to isté a nesmie sa jedno vydávať za druhé.
ASSET="terrain-${REGION_KEY}-${TDEM}-z${TZ}.tar.zst"
REBUILD="${TERRAIN_REBUILD:-false}"

have_tiles() { [ -d terrain-out ] && [ -n "$(ls -A terrain-out 2>/dev/null)" ]; }

if [ "$REBUILD" = 'true' ]; then
  echo "terrain_rebuild=áno – dlaždice sa počítajú nanovo."
  rm -rf terrain-out
elif have_tiles; then
  echo "Výškové dlaždice sú v cache behu ✓"
  TSRC="cache"
else
  # Skús sklad – uložené dlaždice sú lacnejšie než ich prepočítať.
  if python3 workers/drive-store.py --get --store="$TERRAIN_STORE" \
       --name="$ASSET" --dir=/tmp; then
    mkdir -p terrain-out
    tar --use-compress-program=unzstd -xf "/tmp/$ASSET" -C terrain-out
    echo "Výškové dlaždice stiahnuté zo skladu $TERRAIN_STORE ✓"
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
  workers/fetch-dem.sh "$BBOX" "dem/$TDEM" steps-out/terrain.tsv "$TDEM"
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
    # Meno súboru nesie zdroj, tak sa musí prepočítať – inak by sa
    # Sonnyho dlaždice uložili do skladu pod menom toho druhého
    # modelu a nabudúce by sa vydávali za neho.
    ASSET="terrain-${REGION_KEY}-${TDEM}-z${TZ}.tar.zst"
    workers/fetch-dem.sh "$BBOX" "dem/$TDEM" steps-out/terrain.tsv "$TDEM"
  elif [ "$TRC" -ne 0 ]; then
    exit "$TRC"
  fi
  echo "::group::Výškové dlaždice do z$TZ z modelu $TDEM"
  python3 workers/build-terrain.py --dem="dem/$TDEM/all.vrt" --bbox="$BBOX" \
    --minzoom=5 --maxzoom="$TZ" --out=terrain-out
  echo "$TZ" > terrain-out/maxzoom.txt
  echo "::endgroup::"

  # Ulož do skladu, nech ich nabudúce netreba počítať znova. Zlyhanie
  # uloženia NESMIE zhodiť beh – dlaždice sú spočítané a v `terrain-out`,
  # takže mapa bude; stratí sa len to, že sa nabudúce budú počítať znova.
  tar --use-compress-program='zstd -19 -T0' -cf "/tmp/$ASSET" -C terrain-out .
  python3 workers/drive-store.py --put --store="$TERRAIN_STORE" \
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
