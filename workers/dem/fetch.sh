#!/usr/bin/env bash
# Stiahne DEM dlaždice pre bbox zo skladu na Drive a zlepí ich do jedného VRT.
#
# Potrebujú to dva joby – vrstevnice/skaly aj tieňovanie – a kým bol build
# jeden veľký job, stačilo to raz. Po rozdelení na joby by sa to inak
# kopírovalo dvakrát a jedna kópia by časom zaostala za druhou.
#
# SKLAD JE PRIEČINOK NA GOOGLE DRIVE, nie GitHub release. Do releasov sa už
# nepublikuje nič (rozpis: `workers/drive/store.py`); mená súborov ostali tie
# isté, aké mali assety, lebo meno je sľub o rozsahu.
#
# Zdroj hovorí, z ktorého skladu: `sonny` = 1°×1° dlaždice 20 m modelu (sklad
# `dem-sonny`), `dmr35` = tie isté 1°×1° dlaždice, ale z otvorených dát ÚGKK
# (sklad `dem-dmr35`, jemnejšia mriežka), `dmr5` = LLS DMR 5.0, ktoré má dve
# podoby podľa rozsahu (viď nižšie). Všetko sú zrkadlá – build nikdy nesiaha
# priamo na cudzí server, to robia sťahovacie pipeline `Stiahnuť výškové dáta`
# (update-dem.yml), `DMR 5.0 z Drive` (dmr5-drive.yml) – tú si volá build sám
# a je to jediná cesta k DMR 5.0.
#
# `sonny`, `dmr35` a `dmr5` na celý región sa líšia LEN menom skladu:
# dlaždice majú tú istú pomenúvaciu schému (`N49E019.tif`), takže sa nižšie
# nič nevetví.
#
# KTORÝ SKLAD A KTORÉ SÚBORY nerozhoduje tento skript – rozhoduje
# `workers/dem/target.py`, lebo tú istú otázku si kladie aj job `check-dem`
# v build-map.yml a musia si odpovedať rovnako. Kým to bolo napísané dvakrát,
# rozišlo sa to: kontrola hľadala výrez v `dem-ugkk`, kým tieňovanie sťahovalo
# dlaždice z `dem-dmr5` (beh 31307163093).
#
# Použitie:
#   workers/dem/fetch.sh <bbox W,S,E,N> <adresár> [tsv] [zdroj] [kľúč výrezu]
#
# KĽÚČ VÝREZU JE TO, ČO PREPÍNA PODOBU DMR 5.0. Vrstevnice a skaly ho podávajú
# (`contours-build.sh`), tieňovanie nie (build-map.yml, krok „Tieňovanie
# reliéfu“) – to sa robí na celý región, kde 1 m verzia neexistuje. Kto zmení
# jedno z tých volaní, musí zmeniť aj tabuľku vrstiev v `check-dem`.
#
# Výstup:
#   <adresár>/tiles/N49E019.tif …   stiahnuté dlaždice
#   <adresár>/all.vrt               mozaika na čítanie
#
# Očakáva v prostredí prihlásenie na Drive: GDRIVE_CREDENTIALS, alebo premennú
# DRIVE_CLIENT so secretmi DRIVE_SECRET / DRIVE_REFRESH (`client_id` tajný nie
# je, je to repository variable). Stráži to `Lint workflows`.
set -euo pipefail

BBOX="$1"
DIR="${2:-dem}"
STEPS_TSV="${3:-}"
SOURCE="${4:-sonny}"
AREA_KEY="${5:-cely}"
T0=$(date +%s)
# `$HERE` je `workers/dem`, `$WORKERS` je `workers` – sklad je
# v susednom priečinku, `target.py` vedľa tohto skriptu.
HERE="$(dirname "$0")"
WORKERS="$(dirname "$HERE")"
STORE_PY="$WORKERS/drive/store.py"

# Čo sa má stiahnuť, povie jediný zdroj pravdy – ten istý, z ktorého sa pýta
# aj `check-dem`. Sem prídu hotové mená a sklad; vetvenie „ktorá podoba
# DMR 5.0" tu už nie je.
TARGET=$(python3 "$HERE/target.py" \
  --source="$SOURCE" --area-key="$AREA_KEY" --bbox="$BBOX")
get() { printf '%s\n' "$TARGET" | sed -n "s/^$1=//p" | head -1; }
FORM=$(get form)
SRC_STORE=$(get store)
SRC_LABEL=$(get label)

# DMR 5.0 má DVE PODOBY a rozhoduje medzi nimi ROZSAH, nie druhý výber vo
# formulári. Je to jeden a ten istý 1 m LiDAR, len ho nemá zmysel držať
# dvakrát:
#
#   výrez (`area`)   ugkk-<vyrez>.tif v sklade dem-ugkk, plné 1 m rozlíšenie
#   celý región      dlaždice N49E019.tif v dem-dmr5, prevzorkované na 5 m
#
# Dôvod je veľkosť: pri 1 m má jedna 1°×1° dlaždica ~48 GB a runner má
# voľných ~60 GB. Celý región v metri sa teda nemá kde ani rozbaliť – a keďže
# to je obmedzenie stroja a nie voľba, nemá zmysel pýtať sa naň vo formulári.
# Preto tu bývali dva zdroje (`dmr5` a `ugkk`) a je z nich jeden.
if [ "$FORM" = "area" ]; then
  mkdir -p "$DIR"
  UASSET=$(get assets)
  if ! python3 "$STORE_PY" --get --store="$SRC_STORE" --name="$UASSET" \
        --dir="$DIR" --skip-local; then
    # Kód 3 = „pre tento výrez to nemáme", nie „všetko je zle". Volajúci sa
    # podľa neho vie rozhodnúť: buď spadnúť, alebo prejsť na hrubší model
    # (input ugkk_fallback).
    echo "::warning::V sklade $SRC_STORE nie je $UASSET – DMR 5.0 pre tento výrez ešte nikto nevyrobil. Spusti workflow 'DMR 5.0 z Drive (ETRS89)' s area: $AREA_KEY."
    exit 3
  fi
  gdalbuildvrt -q "$DIR/all.vrt" "$DIR/$UASSET"
  SIZE=$(du -h "$DIR/$UASSET" | cut -f1)
  echo "$SRC_LABEL zo skladu: $UASSET, $SIZE"
  gdalinfo "$DIR/$UASSET" | grep -E "Pixel Size|Size is" || true
  if [ -n "$STEPS_TSV" ]; then
    printf '%s\t%s\t%s\t%s\n' 20 "DEM (DMR 5.0, výrez)" "$(( $(date +%s) - T0 ))" \
      "$UASSET zo skladu, $SIZE" >> "$STEPS_TSV"
  fi
  exit 0
fi

# Stiahnuté dlaždice majú vlastný podadresár: medzivýsledky (clip, slope…)
# sú tiež .tif a nesmú sa dostať do mozaiky.
mkdir -p "$DIR/tiles"

# Dlaždice sú 1°×1°, pomenované podľa juhozápadného rohu (konvencia SRTM):
# N49E019. Zoznam počíta dem-target.py – ten istý, z ktorého sa pýta kontrola.
get assets | tr ' ' '\n' | sed '/^$/d' > "$DIR/list.txt"
WANT=$(wc -l < "$DIR/list.txt")
echo "DEM dlaždíc pre bbox: $WANT"

# JEDNO VOLANIE NA VŠETKY DLAŽDICE, nie jedno na každú. Sklad si priečinok
# vypíše raz a potom sťahuje; `--missing-ok`, lebo bbox je obdĺžnik, ale
# produkt pokrýva krajinu – rohové bunky (u Slovenska napr. N47E016
# v Maďarsku) v ňom nikdy nebudú a „chýba jedna, tak spadni" by zhodilo každý
# build pri hranici. `--skip-local`: čo už je v cache behu, sa neťahá znova.
set +e
python3 "$STORE_PY" --get --store="$SRC_STORE" --dir="$DIR/tiles" \
  --name="$(tr '\n' ' ' < "$DIR/list.txt")" --missing-ok --skip-local
SRC=$?
set -e

shopt -s nullglob
tifs=("$DIR"/tiles/*.tif)
have=${#tifs[@]}

if [ "$have" -eq 0 ]; then
  # Kód 3 = „tento model nemáme", nie „všetko je zle" – rovnako ako pri výreze
  # vyššie. Volajúci sa podľa neho vie rozhodnúť: buď spadnúť, alebo prejsť na
  # hrubší model. Pri Sonnym sa už nie je kam vrátiť, tak je to tvrdá chyba;
  # keby aj on vracal 3, volajúci s fallbackom by sa zacyklil.
  echo "::warning::V sklade $SRC_STORE nie je pre toto územie ani jedna dlaždica (kód $SRC)."
  echo "Zálohu z Copernicusu zámerne nepoužívame (je to model povrchu so stromami, nie terén)."
  if [ "$SOURCE" = "dmr5" ]; then
    # Dlaždicovú podobu DMR 5.0 dopĺňa job `mirror-dmr5-tiles` v Build map,
    # ktorý volá `DMR 5.0 z Drive` na presne tie stupne, čo bbox pretína.
    # Keď tu aj tak nič nie je, ten job buď nebežal (kontrola sa rozišla
    # s týmto skriptom – pozri dem-target.py), alebo spadol.
    echo "Doplniť ich mal job 'Doplniť DMR 5.0 (dlaždice)' – pozri jeho log."
    echo "Ručne: workflow 'DMR 5.0 z Drive (ETRS89)', area: $(python3 "$HERE/target.py" --source=dmr5 --bbox="$BBOX" | sed -n 's/^degrees=//p'), tiles: true, mriežka 5 m."
  else
    echo "Spusti workflow 'Stiahnuť výškové dáta' so zdrojom, ktorý toto územie pokrýva."
  fi
  [ "$SOURCE" = "sonny" ] && exit 1
  exit 3
fi
if [ "$have" -lt "$WANT" ]; then
  # Bbox je obdĺžnik, produkt pokrýva krajinu – rohové bunky za hranicou
  # v ňom byť nemusia. Tam jednoducho nebude terén; radšej diera, ktorú
  # vidno, než výplň z modelu povrchu.
  echo "::warning::V sklade $SRC_STORE nie je $(( WANT - have )) z $WANT dlaždíc – tam vrstevnice, skaly ani tieňovanie nebudú. Ak to územie má mať terén, spusti 'Update DEM' s priečinkom, ktorý ho pokrýva."
fi
echo "$SRC_LABEL: $have z $WANT dlaždíc zo skladu $SRC_STORE ✓"

# -resolution highest: dlaždice môžu mať rôznu mriežku (20m model má
# obdĺžnikové pixely) – mozaika ide na to jemnejšie.
gdalbuildvrt -q -resolution highest "$DIR/all.vrt" "${tifs[@]}"
echo "DEM dlaždíc k dispozícii: ${#tifs[@]} → $DIR/all.vrt"

if [ -n "$STEPS_TSV" ]; then
  # Prvé pole je poradie v súhrne – joby bežia súbežne, tak sa riadky
  # neradia podľa času, ale podľa toho, kam v pipeline patria.
  printf '%s\t%s\t%s\t%s\n' 20 "DEM dlaždice ($SRC_LABEL)" "$(( $(date +%s) - T0 ))" \
    "$have z $WANT dlaždíc, $(du -sh "$DIR/tiles" | cut -f1)" >> "$STEPS_TSV"
fi
