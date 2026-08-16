#!/usr/bin/env bash
# Základná mapa sveta → `_site` (dlaždice, štýly, glyfy, manifest).
#
# PREČO SAMOSTATNÝ SKRIPT A NIE `run:` VO WORKFLOWE: súbor s workflowom má
# strop 128 KiB a nad ním ho GitHub ticho NEPRIJME (pravidlo 3 v CLAUDE.md).
# Bokom od toho sa to takto dá spustiť lokálne a nie „pushni a pozri sa".
#
# ČO ROBÍ, V PORADÍ:
#   1. nástroje (GDAL kvôli vodným plochám, Planetiler)
#   2. podklady        `workers/world/sources.py` → data/world/
#   3. dlaždice        Planetiler nad `workers/world/world.yml`
#   4. glyfy a štýly   `workers/assets/glyphs.sh`, `workers/world/style.mjs`
#   5. manifest        čo v tejto mape je – jediný súbor, z ktorého to appka
#                      aj `workers/deploy/publish-map.py` zistia
#
# Hodnoty z prostredia (viď job „Mapa sveta" vo `world-map.yml`):
#   REGION_KEY OPT_MAXZOOM OPT_LIMIT_MB GLYPHS_ZIP
# Bbox a ľudské meno sa NEPODÁVAJÚ – sú v `workers/data/regions.json` a druhá
# kópia by sa raz rozišla (pravidlo 1).
set -euo pipefail

T_CELKOM=$(date +%s)
mkdir -p _site/tiles _site/styles data/world steps-out

REGION="$REGION_KEY"
NAME=$(jq -r --arg r "$REGION" '.[$r].name // ""' workers/data/regions.json)
BBOX=$(jq -r --arg r "$REGION" '.[$r].bbox // [] | join(",")' workers/data/regions.json)
if [ -z "$NAME" ] || [ -z "$BBOX" ]; then
  echo "::error::Región '$REGION' nie je vo workers/data/regions.json (alebo nemá meno a bbox). Mapa sveta má kľúč `svet` – iný sem nepatrí."
  exit 1
fi

# Planetiler vie dlaždice najviac po zoom 16; svet ich toľko nepotrebuje ani
# omylom – pri z8 má vodstvo stovky MB a mapa je stále len podklad pod výber
# regiónu. Preto je strop 8 a dno 3.
Z="$OPT_MAXZOOM"
case "$Z" in ''|*[!0-9]*) Z=6 ;; esac
if [ "$Z" -gt 8 ]; then
  echo "::warning::maxzoom $Z je na mapu sveta priveľa (vodstvo rastie zhruba 3× na úroveň a mapa je len podklad pod výber regiónu). Používam 8."
  Z=8
fi
if [ "$Z" -lt 3 ]; then Z=3; fi

LIMIT_MB="$OPT_LIMIT_MB"
case "$LIMIT_MB" in ''|*[!0-9]*) LIMIT_MB=250 ;; esac

echo "Mapa sveta: $NAME ($BBOX), maxzoom $Z, strop veľkosti ${LIMIT_MB} MB"

# ---------- 1. nástroje ----------
# GDAL kvôli `ogr2ogr` – vodné plochy z OSM sú shapefile v EPSG:3857 a treba
# ich premietnuť (rozpis vo `workers/world/sources.py`).
T0=$(date +%s)
if ! command -v ogr2ogr >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq gdal-bin
fi
workers/lib/planetiler.sh
echo "Nástroje hotové za $(( $(date +%s) - T0 )) s"

# ---------- 2. podklady ----------
# Sťahovanie z cudzích serverov a prevod. Skript si píše vlastný plán
# s odhadom aj postup – hodina ticha v logu sa nedá odlíšiť od zaseknutia.
T_SRC=$(date +%s)
echo "::group::Podklady (vodstvo, hranice, štáty, jazerá, regióny sťahovania)"
python3 workers/world/sources.py --out=data/world
echo "::endgroup::"
echo "Podklady: $(du -sh data/world | cut -f1) za $(( $(date +%s) - T_SRC )) s"

# ---------- 3. dlaždice ----------
# ČO MÁ V SCHÉME VYŠŠÍ `min_zoom`, NEŽ JE MAXZOOM ARCHÍVU, PLANETILER ZAHODÍ –
# a nepovie o tom nič. Presne to raz zmazalo z mapy všetky ploty (rozpis
# v `workers/features/features.yml`), tak sa to tu porovná vopred.
NAJVYSSI=$(python3 -c "
import yaml
d = yaml.safe_load(open('workers/world/world.yml'))
print(max((f.get('min_zoom', 0) for l in d['layers'] for f in l['features']),
          default=0))
")
if [ "$NAJVYSSI" -gt "$Z" ]; then
  echo "::warning::Schéma má prvky až od zoomu $NAJVYSSI, ale dlaždice sa robia po $Z – tie prvky (najmä výseky regiónov sťahovania) v mape NEBUDÚ. Zdvihni maxzoom na $NAJVYSSI, alebo to ber ako zámer."
fi

T_PM=$(date +%s)
OUT="_site/tiles/${REGION}.pmtiles"
echo "::group::Planetiler – mapa sveta, maxzoom $Z"
# `--simplify_tolerance` a `--min_feature_size` sa NECHÁVAJÚ NA PREDVOLENÝCH
# HODNOTÁCH – naopak než pri vrstevniciach, kde sa vypínajú. Tam ide o presný
# tvar terénu, tu o prehľad sveta: neusporiadané ostrovčeky veľkosti pixela
# nikto neuvidí a zaplatí sa za ne miestom v balíku.
java -Xmx4g -jar planetiler.jar generate-custom \
  --schema=workers/world/world.yml \
  --output="$OUT" \
  --minzoom=0 --maxzoom="$Z" --render_maxzoom="$Z" \
  --force
echo "::endgroup::"

MB=$(( $(stat -c%s "$OUT") / 1048576 ))
echo "Dlaždice: $(du -h "$OUT" | cut -f1) (${MB} MB) za $(( $(date +%s) - T_PM )) s"
if [ "$MB" -gt "$LIMIT_MB" ]; then
  echo "::warning::Mapa sveta má ${MB} MB, čo je nad stropom ${LIMIT_MB} MB. Zníž maxzoom (teraz $Z) – vodstvo rastie zhruba 3× na úroveň – alebo zdvihni input `limit_mb`, ak je taký balík v poriadku."
fi

# ---------- 4. glyfy a štýly ----------
# Glyfy sťahuje TEN ISTÝ skript ako pri mape kraja: „ako sa dostanú fonty do
# `_site`" je jedna otázka a dve kópie by sa raz rozišli. Keď sa nestiahnu,
# štýl siahne na verejnú službu a mapa ide ďalej.
T_A=$(date +%s)
workers/assets/glyphs.sh
# KURZÍVA IDE PREČ. Jeden fontstack má ~34 MB (celý unicode), takže tri sú
# 102 MB – pri mape, ktorej dlaždice majú desiatky MB, je to väčšina balíka.
# Štýl sveta má popisky len v dvoch rezoch (`Noto Sans Regular` a `Bold`,
# viď `workers/world/style.mjs`), tak sa tretí nebalí. Mapa kraja si berie
# všetky tri – tam kurzívu používajú vodné toky a geografické názvy.
rm -rf "_site/fonts/Noto Sans Italic"
node workers/world/style.mjs --out=_site/styles --region="$REGION" --maxzoom="$Z"
echo "Glyfy ($(du -sh _site/fonts 2>/dev/null | cut -f1)) a štýly za $(( $(date +%s) - T_A )) s"

# ---------- 5. manifest ----------
# JEDINÝ SÚBOR, Z KTORÉHO SA DÁ ZISTIŤ, ČO V TEJTO MAPE JE: číta ho appka aj
# `workers/deploy/publish-map.py` (a cez neho sa z neho skladá položka
# v `maps.json` – bbox, maxzoom a cesta k dlaždiciam). Tvar je ten istý ako
# pri mape kraja (`workers/deploy/site.sh`), lebo ho číta ten istý kód; polia
# o vrstvách, ktoré svet nemá (vrstevnice, skaly, terén), v ňom jednoducho
# nie sú – prázdna hodnota by znamenala „vrstva je, len je prázdna".
# Ktorá téma je v ktorom súbore. Kľúč je téma (`svetla`, `tmava`, …), hodnota
# cesta v balíku – appka tak nemusí mená štýlov skladať a uhádnuť.
STYLY=$(find _site/styles -name '*.json' -printf '%f\n' | sort \
  | jq -R -s --arg r "$REGION" \
      'split("\n") | map(select(length > 0))
       | map({key: (sub("^\($r)-"; "") | sub("\\.json$"; "")),
              value: ("styles/" + .)}) | from_entries')
jq -n \
  --arg region "$REGION" \
  --arg name "$NAME" \
  --arg bbox "$BBOX" \
  --arg built "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson maxzoom "$Z" \
  --argjson size_mb "$MB" \
  --argjson styles "$STYLY" \
  '{
    default_region: $region,
    kind: "svet",
    built_at: $built,
    maxzoom: $maxzoom,
    glyphs: "fonts/{fontstack}/{range}.pbf",
    styles: $styles,
    default_style: ("styles/" + $region + "-svetla.json"),
    attribution: "© OpenStreetMap prispievatelia, Geofabrik, Natural Earth",
    regions: {
      ($region): {
        name: $name,
        bbox: ($bbox | split(",") | map(tonumber)),
        pmtiles: ("tiles/" + $region + ".pmtiles"),
        maxzoom: $maxzoom,
        size_mb: $size_mb
      }
    }
  }' > _site/tiles/manifest.json
cat _site/tiles/manifest.json

echo "maxzoom=$Z" >> "$GITHUB_OUTPUT"
echo "size_mb=$MB" >> "$GITHUB_OUTPUT"
echo "name=$NAME" >> "$GITHUB_OUTPUT"
du -sh _site
printf '%s\t%s\t%s\t%s\n' "10" "Mapa sveta" "$(( $(date +%s) - T_CELKOM ))" \
  "maxzoom $Z, ${MB} MB" >> steps-out/world.tsv
