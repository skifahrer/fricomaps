#!/usr/bin/env bash
# Stiahne DEM dlaždice pre bbox z GitHub releasu a zlepí ich do jedného VRT.
#
# Potrebujú to dva joby – vrstevnice/skaly aj tieňovanie – a kým bol build
# jeden veľký job, stačilo to raz. Po rozdelení na joby by sa to inak
# kopírovalo dvakrát a jedna kópia by časom zaostala za druhou.
#
# Zdroj hovorí, z ktorého releasu: `sonny` = 1°×1° dlaždice 20 m modelu
# (release `dem-sonny`), `ugkk` = jeden COG s 1 m LiDARom pre výrez
# (release `dem-ugkk`). Oboje sú zrkadlá – build nikdy nesiaha priamo na
# cudzí server, to robia workflowy `Update DEM` a `Update DEM (ÚGKK 1 m)`.
#
# Použitie:
#   workers/fetch-dem.sh <bbox W,S,E,N> <adresár> [tsv] [zdroj] [kľúč výrezu]
# Výstup:
#   <adresár>/tiles/N49E019.tif …   stiahnuté dlaždice
#   <adresár>/all.vrt               mozaika na čítanie
#
# Očakáva v prostredí: DEM_RELEASE, GITHUB_REPOSITORY, GH_TOKEN.
set -euo pipefail

BBOX="$1"
DIR="${2:-dem}"
STEPS_TSV="${3:-}"
SOURCE="${4:-sonny}"
AREA_KEY="${5:-cely}"
T0=$(date +%s)

if [ "$SOURCE" = "ugkk" ]; then
  # 1 m LiDAR je v release ako jeden COG na výrez. Doplniť ho tam mal job
  # `mirror-dem-ugkk`; keď tam nie je, niečo v tom reťazci zlyhalo a build
  # to má povedať, nie ticho pokračovať so zlým modelom.
  mkdir -p "$DIR"
  UASSET="ugkk-${AREA_KEY}.tif"
  if ! gh release download "${UGKK_RELEASE:-dem-ugkk}" --repo "$GITHUB_REPOSITORY" \
        --pattern "$UASSET" --dir "$DIR" --clobber >/dev/null 2>&1; then
    # Kód 3 = „ÚGKK nemáme", nie „všetko je zle". Volajúci sa podľa neho vie
    # rozhodnúť: buď spadnúť, alebo prejsť na Sonnyho (input ugkk_fallback).
    echo "::warning::V release ${UGKK_RELEASE:-dem-ugkk} nie je $UASSET – 1 m LiDAR pre tento výrez nemáme. Pozri log jobu 'Doplniť ÚGKK 1 m LiDAR'."
    exit 3
  fi
  gdalbuildvrt -q "$DIR/all.vrt" "$DIR/$UASSET"
  SIZE=$(du -h "$DIR/$UASSET" | cut -f1)
  echo "ÚGKK DMR 5.0 z releasu: $UASSET, $SIZE"
  gdalinfo "$DIR/$UASSET" | grep -E "Pixel Size|Size is" || true
  if [ -n "$STEPS_TSV" ]; then
    printf '%s\t%s\t%s\t%s\n' 20 "DEM (ÚGKK 1 m)" "$(( $(date +%s) - T0 ))" \
      "$UASSET z releasu, $SIZE" >> "$STEPS_TSV"
  fi
  exit 0
fi

IFS=, read -r W S E N <<< "$BBOX"
# Stiahnuté dlaždice majú vlastný podadresár: medzivýsledky (clip, slope…)
# sú tiež .tif a nesmú sa dostať do mozaiky.
mkdir -p "$DIR/tiles"

# Dlaždice sú 1°×1°, pomenované podľa juhozápadného rohu (konvencia SRTM):
# N49E019.
python3 - "$W" "$S" "$E" "$N" > "$DIR/list.txt" <<'PY'
import math, sys
w, s, e, n = map(float, sys.argv[1:5])
for lat in range(math.floor(s), math.floor(n) + 1):
    for lon in range(math.floor(w), math.floor(e) + 1):
        ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
        print(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")
PY
WANT=$(wc -l < "$DIR/list.txt")
echo "DEM dlaždíc pre bbox: $WANT"

# Zoznam dlaždíc v release si vypýtame naraz – nemá zmysel skúšať sťahovať
# to, čo tam nie je.
ASSETS=$(gh release view "$DEM_RELEASE" --repo "$GITHUB_REPOSITORY" \
  --json assets -q '.assets[].name' 2>/dev/null || echo '')

have=0
missing=""
while IFS= read -r t; do
  if [ -s "$DIR/tiles/$t.tif" ]; then
    have=$(( have + 1 ))          # už v cache behu
  elif printf '%s\n' "$ASSETS" | grep -qx "$t.tif" && \
       gh release download "$DEM_RELEASE" --repo "$GITHUB_REPOSITORY" \
         --pattern "$t.tif" --dir "$DIR/tiles" --clobber >/dev/null 2>&1; then
    have=$(( have + 1 ))
  else
    missing="$missing $t"
  fi
done < "$DIR/list.txt"

if [ "$have" -eq 0 ]; then
  echo "::error::V release $DEM_RELEASE nie je pre toto územie ani jedna dlaždica."
  echo "Zálohu z Copernicusu zámerne nepoužívame (je to model povrchu so stromami, nie terén)."
  echo "Spusti workflow 'Update DEM' s priečinkom, ktorý toto územie pokrýva – alebo mu vyplň direct_urls."
  exit 1
fi
if [ -n "$missing" ]; then
  # Bbox je obdĺžnik, produkt pokrýva krajinu – rohové bunky za hranicou
  # v ňom byť nemusia. Tam jednoducho nebude terén; radšej diera, ktorú
  # vidno, než výplň z modelu povrchu.
  echo "::warning::V release $DEM_RELEASE nie sú dlaždice:$missing – tam vrstevnice, skaly ani tieňovanie nebudú. Ak to územie má mať terén, spusti 'Update DEM' s priečinkom, ktorý ho pokrýva."
fi
echo "Sonny's LiDAR DTM: $have z $WANT dlaždíc z release $DEM_RELEASE ✓"

shopt -s nullglob
tifs=("$DIR"/tiles/*.tif)
if [ ${#tifs[@]} -eq 0 ]; then
  echo "::error::Nepodarilo sa získať žiadnu DEM dlaždicu pre bbox $BBOX."
  exit 1
fi

# -resolution highest: dlaždice môžu mať rôznu mriežku (20m model má
# obdĺžnikové pixely) – mozaika ide na to jemnejšie.
gdalbuildvrt -q -resolution highest "$DIR/all.vrt" "${tifs[@]}"
echo "DEM dlaždíc k dispozícii: ${#tifs[@]} → $DIR/all.vrt"

if [ -n "$STEPS_TSV" ]; then
  # Prvé pole je poradie v súhrne – joby bežia súbežne, tak sa riadky
  # neradia podľa času, ale podľa toho, kam v pipeline patria.
  printf '%s\t%s\t%s\t%s\n' 20 "DEM dlaždice (Sonny)" "$(( $(date +%s) - T0 ))" \
    "$have z $WANT dlaždíc, $(du -sh "$DIR/tiles" | cut -f1)" >> "$STEPS_TSV"
fi
