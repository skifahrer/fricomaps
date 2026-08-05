#!/usr/bin/env bash
# Stiahne DEM dlaždice pre bbox z GitHub releasu a zlepí ich do jedného VRT.
#
# Potrebujú to dva joby – vrstevnice/skaly aj tieňovanie – a kým bol build
# jeden veľký job, stačilo to raz. Po rozdelení na joby by sa to inak
# kopírovalo dvakrát a jedna kópia by časom zaostala za druhou.
#
# Zdroj hovorí, odkiaľ: `sonny` = naše zrkadlo v releasi (20 m, overené),
# `ugkk` = 1 m LiDAR od ÚGKK cez ich ArcGIS ImageServer (viď
# workers/fetch-dem-ugkk.py a workflow „Check DEM source").
#
# Použitie:
#   workers/fetch-dem.sh <bbox W,S,E,N> <adresár> [tsv na meranie] [zdroj]
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
T0=$(date +%s)

if [ "$SOURCE" = "ugkk" ]; then
  # 1 m LiDAR sa nesťahuje po 1° dlaždiciach, ale po výrezoch cez ImageServer.
  exec python3 workers/fetch-dem-ugkk.py --bbox="$BBOX" --out="$DIR" \
    ${STEPS_TSV:+--steps-tsv="$STEPS_TSV"}
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
